from __future__ import annotations

import json
import os
import tarfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

from app.core.config import cfg
from app.db.conn import db

# ---- Internal utils ----


def _now_ts() -> int:
    return int(time.time())


def _read_ts(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int((f.read() or "0").strip())
    except Exception:
        return None


def _write_ts(path: str, ts: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(int(ts)))


def _bytes_sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _disk_free_ratio(dir_path: Path) -> float:
    import shutil

    usage = shutil.disk_usage(str(dir_path))
    if usage.total == 0:
        return 0.0
    return usage.free / usage.total


# ---- Public API ----


@dataclass
class BackupObject:
    path: str
    size_bytes: int
    sha256: str


@dataclass
class BackupMeta:
    backup_id: str
    type: Literal["full", "incremental"]
    started_at_utc: int
    finished_at_utc: int
    status: Literal["success", "failed"]
    manifest_path: str
    objects_count: int
    bytes_total: int


def backup_recent(now: Optional[int] = None) -> bool:
    """
    Политика свежести бэкапа согласно L3_Common §5:
    - есть full backup моложе 24 часов
    - есть последний incremental моложе 60 минут

    Источник данных: таблица system_backups (migrations/001_init.sql) и файлы‑маркеры.
    """
    now_i = int(now or _now_ts())
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT last_full_ts_utc, last_inc_ts_utc FROM system_backups WHERE id=1"
            ).fetchone()
            full_ts = row[0] if row else None
            inc_ts = row[1] if row else None
    except Exception:
        # Fallback to markers on any issue
        full_ts = None
        inc_ts = None

    # Fallback to on-disk markers for compatibility/tests
    base = os.environ.get("APP_VAR_DIR", cfg.data_dir)
    bdir = os.path.join(base, "backup")
    if full_ts is None:
        full_ts = _read_ts(os.path.join(bdir, "recent_full.ts"))
    if inc_ts is None:
        inc_ts = _read_ts(os.path.join(bdir, "recent_incr.ts"))

    if full_ts is None or inc_ts is None:
        return False
    full_ok = (now_i - int(full_ts)) <= 24 * 3600
    incr_ok = (now_i - int(inc_ts)) <= 60 * 60
    return bool(full_ok and incr_ok)


def backup_health_ok() -> tuple[bool, str | None]:
    """Health-check перед тяжёлыми действиями/бэкапом (см. L3_Common §5)."""
    var_dir = Path(cfg.data_dir)
    # BACKUP_PATH доступность
    bdir = var_dir / "backup"
    try:
        bdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f"E_BACKUP_PATH: {e}"
    # свободное место ≥ 20%
    if _disk_free_ratio(var_dir) < 0.20:
        return False, "E_BACKUP_NO_SPACE"
    return True, None


def _collect_objects(backup_type: Literal["full", "incremental"]) -> List[BackupObject]:
    objects: List[BackupObject] = []
    # Always include SQLite DB
    db_path = Path(cfg.sqlite_path)
    if db_path.exists():
        sha = _bytes_sha256(db_path)
        objects.append(BackupObject(str(db_path), db_path.stat().st_size, sha))

    # For full backups also include materials and submissions blobs
    if backup_type == "full":
        for rel in ("materials", "submissions"):
            root = Path(os.environ.get("APP_VAR_DIR", cfg.data_dir)) / rel
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        sha = _bytes_sha256(p)
                        objects.append(BackupObject(str(p), p.stat().st_size, sha))
                    except Exception:
                        # continue best-effort
                        pass
    return objects


def _write_manifest(
    manifest_dir: Path, meta: BackupMeta, objects: List[BackupObject]
) -> Path:
    manifest_dir.mkdir(parents=True, exist_ok=True)
    mpath = manifest_dir / f"{meta.backup_id}.json"
    payload: Dict[str, object] = {
        "meta": asdict(meta),
        "objects": [asdict(o) for o in objects],
    }
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return mpath


def _create_archive(
    archive_dir: Path, backup_id: str, objects: List[BackupObject]
) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    apath = archive_dir / f"{backup_id}.tar.gz"
    with tarfile.open(apath, "w:gz") as tar:
        for o in objects:
            p = Path(o.path)
            if not p.exists():
                continue
            # Store relative to filesystem root to preserve unique path but stay portable
            tar.add(
                str(p), arcname=str(p.relative_to("/")) if p.is_absolute() else str(p)
            )
    return apath


def trigger_backup(
    backup_type: Literal["auto", "full", "incremental"] = "auto"
) -> BackupMeta:
    """
    Выполняет бэкап согласно политике:
    - auto: full если нет свежего full за 24h, иначе incremental
    - full: вся БД + все blobs (materials, submissions)
    - incremental: только БД (упрощённая стратегия)

    Создаёт архив и manifest в var/backup, обновляет system_backups и маркеры.
    """
    ok, err = backup_health_ok()
    if not ok:
        raise RuntimeError(err or "E_BACKUP_HEALTH")

    var_dir = Path(os.environ.get("APP_VAR_DIR", cfg.data_dir))
    bdir = var_dir / "backup"
    manifests = bdir / "manifests"
    archives = bdir / "archives"

    # Decide type for auto
    if backup_type == "auto":
        # If last full older than 24h → full, else incremental
        with db() as conn:
            row = conn.execute(
                "SELECT last_full_ts_utc FROM system_backups WHERE id=1"
            ).fetchone()
        last_full = int(row[0]) if row and row[0] else 0
        backup_type = "full" if (_now_ts() - last_full) > 24 * 3600 else "incremental"

    started = _now_ts()
    backup_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{backup_type}"
    )

    # Collect objects and write archive + manifest
    objects = _collect_objects(backup_type)
    archive_path = _create_archive(archives, backup_id, objects)
    meta = BackupMeta(
        backup_id=backup_id,
        type=backup_type,  # type: ignore[arg-type]
        started_at_utc=started,
        finished_at_utc=_now_ts(),
        status="success",
        manifest_path=str((manifests / f"{backup_id}.json").resolve()),
        objects_count=len(objects),
        bytes_total=int(archive_path.stat().st_size) if archive_path.exists() else 0,
    )
    mpath = _write_manifest(manifests, meta, objects)

    # Update DB timestamps and on-disk markers
    with db() as conn:
        if backup_type == "full":
            # After a fresh full backup, also consider incremental satisfied now
            conn.execute(
                "UPDATE system_backups SET last_full_ts_utc=?, last_inc_ts_utc=?, updated_at_utc=strftime('%s','now') WHERE id=1",
                (meta.finished_at_utc, meta.finished_at_utc),
            )
        else:
            conn.execute(
                "UPDATE system_backups SET last_inc_ts_utc=?, updated_at_utc=strftime('%s','now') WHERE id=1",
                (meta.finished_at_utc,),
            )
        conn.commit()

    if backup_type == "full":
        _write_ts(str(bdir / "recent_full.ts"), meta.finished_at_utc)
        _write_ts(str(bdir / "recent_incr.ts"), meta.finished_at_utc)
    else:
        _write_ts(str(bdir / "recent_incr.ts"), meta.finished_at_utc)

    # Also write a pointer for last manifest path for quick checks
    _write_ts(str(bdir / "last_ok.ts"), meta.finished_at_utc)
    # store small pointer file
    with open(bdir / "last_manifest.path", "w", encoding="utf-8") as f:
        f.write(str(mpath))

    return meta
