from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.core.files import MATERIALS_DIR, link_or_copy, move_file, safe_filename

# В проекте подключение к БД — так:
from app.db.conn import db

# ---------- MODELS ----------


@dataclass
class Material:
    id: int
    week_id: int
    path: str
    sha256: str
    size_bytes: int
    mime: Optional[str]
    uploaded_by: str
    created_at_utc: int
    week_no: Optional[int] = None
    visibility: Optional[str] = None
    type: Optional[str] = None  # 'p','m','n','s','v'
    is_active: Optional[int] = None  # 1 or 0
    version: Optional[int] = None


# ---------- MATERIALS (недельные, с видимостью) ----------


def list_materials_by_week(week_no: int, audience: str = "student") -> List[Material]:
    """
    Вернёт материалы по неделе с фильтром видимости.
    audience: 'student' | 'teacher'
    - student видит только visibility='public'
    - teacher/owner видит всё
    """
    with db() as conn:
        if audience == "teacher":
            rows = conn.execute(
                """
                SELECT m.id, m.week_id, m.path, m.sha256, m.size_bytes, m.mime,
                       m.uploaded_by, m.created_at_utc, w.week_no, m.visibility,
                       m.type, m.is_active, m.version
                FROM materials m
                JOIN weeks w ON w.id = m.week_id
                WHERE w.week_no = ? AND m.is_active = 1
                ORDER BY m.id ASC
                """,
                (week_no,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.id, m.week_id, m.path, m.sha256, m.size_bytes, m.mime,
                       m.uploaded_by, m.created_at_utc, w.week_no, m.visibility,
                       m.type, m.is_active, m.version
                FROM materials m
                JOIN weeks w ON w.id = m.week_id
                WHERE w.week_no = ?
                  AND m.visibility = 'public'
                  AND m.is_active = 1
                ORDER BY m.id ASC
                """,
                (week_no,),
            ).fetchall()
    return [Material(*row) for row in rows]


def insert_week_material_file(
    week_no: int,
    uploaded_by: str,
    path: str,
    sha256: str,
    size_bytes: int,
    mime: Optional[str],
    visibility: str = "public",
    type: str = "p",
    original_name: Optional[str] = None,
) -> int:
    """Insert material for week with versioning.

    - If duplicate content (sha256+size) exists anywhere, returns -1.
    - Sets new row as active and bumps version = (max(version) + 1) per (week_id,type).
    - Previous active (if any) for (week_id,type) becomes archived (is_active=0).
    """
    assert visibility in ("public", "teacher_only")
    assert type in ("p", "m", "n", "s", "v")
    with db() as conn:
        wk = conn.execute("SELECT id FROM weeks WHERE week_no=?", (week_no,)).fetchone()
        if not wk:
            raise ValueError("unknown week_no")
        week_id = int(wk[0])
        # Compute next version for this (week_id,type)
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM materials WHERE week_id=? AND type=?",
            (week_id, type),
        ).fetchone()
        next_ver = int(row[0] or 0) + 1

        # Prepare filesystem layout
        fname = safe_filename(original_name or os.path.basename(path) or "material.bin")
        base_dir = os.path.join(MATERIALS_DIR, f"W{week_no}", type)
        active_dir = os.path.join(base_dir, "active")
        active_path = os.path.join(active_dir, fname)

        # Detect duplicate content BEFORE any state change to avoid losing active
        dup = conn.execute(
            "SELECT id, week_id, type, is_active, path, version FROM materials WHERE sha256=? AND size_bytes=? LIMIT 1",
            (sha256, size_bytes),
        ).fetchone()
        if dup:
            dup_id = int(dup[0])
            dup_week_id = int(dup[1])
            dup_type = str(dup[2])
            dup_active = int(dup[3] or 0)
            dup_path = str(dup[4]) if dup[4] is not None else None
            # Same (week,type): if already active — skip; if archived — promote to active with new version
            if dup_week_id == week_id and dup_type == type:
                if dup_active == 1:
                    return -1
                # Archive previous active (if any)
                prev = conn.execute(
                    "SELECT id, path, version FROM materials WHERE week_id=? AND type=? AND is_active=1 LIMIT 1",
                    (week_id, type),
                ).fetchone()
                if prev:
                    prev_id = int(prev[0])
                    prev_path = str(prev[1])
                    prev_ver = int(prev[2] or 1)
                    prev_name = os.path.basename(prev_path) or fname
                    archive_path_prev = os.path.join(
                        base_dir, f"v{prev_ver}", prev_name
                    )
                    try:
                        move_file(prev_path, archive_path_prev)
                    except Exception:
                        pass
                    conn.execute(
                        "UPDATE materials SET is_active=0, path=? WHERE id=?",
                        (archive_path_prev, prev_id),
                    )
                # Promote archived duplicate to active
                try:
                    if dup_path and os.path.exists(dup_path):
                        move_file(dup_path, active_path)
                    else:
                        link_or_copy(path, active_path)
                except Exception:
                    try:
                        link_or_copy(path, active_path)
                    except Exception:
                        return -1
                conn.execute(
                    (
                        "UPDATE materials SET is_active=1, path=?, mime=?, visibility=?, "
                        "uploaded_by=?, created_at_utc=strftime('%s','now'), version=? WHERE id=?"
                    ),
                    (
                        active_path,
                        mime,
                        visibility,
                        uploaded_by,
                        next_ver,
                        dup_id,
                    ),
                )
                return dup_id
            # Duplicate exists elsewhere (global unique index) — skip
            return -1

        # No duplicates: archive previous active and insert a new row
        prev = conn.execute(
            "SELECT id, path, version FROM materials WHERE week_id=? AND type=? AND is_active=1 LIMIT 1",
            (week_id, type),
        ).fetchone()
        if prev:
            prev_id = int(prev[0])
            prev_path = str(prev[1])
            prev_ver = int(prev[2] or 1)
            prev_name = os.path.basename(prev_path) or fname
            archive_path_prev = os.path.join(base_dir, f"v{prev_ver}", prev_name)
            try:
                move_file(prev_path, archive_path_prev)
            except Exception:
                pass
            conn.execute(
                "UPDATE materials SET is_active=0, path=? WHERE id=?",
                (archive_path_prev, prev_id),
            )

        try:
            # Materialize new active file (hardlink or copy from blob path)
            link_or_copy(path, active_path)
            cur = conn.execute(
                """
                INSERT INTO materials(
                  week_id, path, sha256, size_bytes,
                  mime, visibility, uploaded_by, created_at_utc,
                  type, is_active, version
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, strftime('%s','now'), ?, 1, ?)
                """,
                (
                    week_id,
                    active_path,
                    sha256,
                    size_bytes,
                    mime,
                    visibility,
                    uploaded_by,
                    type,
                    next_ver,
                ),
            )
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            # Safety net: treat any race/dup as already exists
            return -1


def get_active_material(week_id: int, type: str) -> Optional[Material]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT m.id, m.week_id, m.path, m.sha256, m.size_bytes, m.mime,
                   m.uploaded_by, m.created_at_utc, w.week_no, m.visibility,
                   m.type, m.is_active, m.version
            FROM materials m
            JOIN weeks w ON w.id = m.week_id
            WHERE m.week_id=? AND m.type=? AND m.is_active=1
            LIMIT 1
            """,
            (week_id, type),
        ).fetchone()
    return Material(*row) if row else None


def list_material_versions(week_id: int, type: str, limit: int = 20) -> List[Material]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.week_id, m.path, m.sha256, m.size_bytes, m.mime,
                   m.uploaded_by, m.created_at_utc, w.week_no, m.visibility,
                   m.type, m.is_active, m.version
            FROM materials m
            JOIN weeks w ON w.id = m.week_id
            WHERE m.week_id=? AND m.type=?
            ORDER BY m.version DESC, m.id DESC
            LIMIT ?
            """,
            (week_id, type, limit),
        ).fetchall()
    return [Material(*r) for r in rows]


def archive_active(week_id: int, type: str) -> bool:
    """Deactivate current active version for (week_id,type). Returns True if changed something."""
    with db() as conn:
        row = conn.execute(
            """
            SELECT m.id, m.path, m.version, w.week_no
            FROM materials m JOIN weeks w ON w.id=m.week_id
            WHERE m.week_id=? AND m.type=? AND m.is_active=1
            LIMIT 1
            """,
            (week_id, type),
        ).fetchone()
        if not row:
            return False
        mid = int(row[0])
        cur_path = str(row[1])
        ver = int(row[2] or 1)
        week_no = int(row[3])
        base_dir = os.path.join(MATERIALS_DIR, f"W{week_no}", type)
        new_path = os.path.join(
            base_dir, f"v{ver}", os.path.basename(cur_path) or "material.bin"
        )
        try:
            move_file(cur_path, new_path)
        except Exception:
            pass
        conn.execute(
            "UPDATE materials SET is_active=0, path=? WHERE id=?",
            (new_path, mid),
        )
        return True


def delete_archived(week_id: int, type: Optional[str] = None) -> int:
    """Delete archived versions. If type is None, deletes for all types of the week.
    Returns number of deleted rows.
    """
    with db() as conn:
        if type is None:
            rows = conn.execute(
                "SELECT id, path FROM materials WHERE week_id=? AND is_active=0",
                (week_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, path FROM materials WHERE week_id=? AND type=? AND is_active=0",
                (week_id, type),
            ).fetchall()
        ids = [int(r[0]) for r in rows]
        for _, p in rows:
            try:
                os.remove(str(p))
            except Exception:
                pass
        if not ids:
            return 0
        qmarks = ",".join(["?"] * len(ids))
        conn.execute(f"DELETE FROM materials WHERE id IN ({qmarks})", ids)
        return len(ids)


def enforce_archive_limit(week_id: int, type: str, max_versions: int = 20) -> int:
    """Ensure that total versions for (week_id,type) do not exceed max_versions.
    If exceed, delete oldest archived versions after making a backup outside of this function.
    Returns number of deleted rows.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(1) FROM materials WHERE week_id=? AND type=?",
            (week_id, type),
        ).fetchone()
        total = int(row[0] or 0)
        if total <= max_versions:
            return 0
        to_delete = total - max_versions
        # Fetch oldest archived candidates
        rows = conn.execute(
            (
                "SELECT id, path FROM materials "
                "WHERE week_id=? AND type=? AND is_active=0 "
                "ORDER BY version ASC, id ASC LIMIT ?"
            ),
            (week_id, type, to_delete),
        ).fetchall()
        ids = [int(r[0]) for r in rows]
        for _, p in rows:
            try:
                os.remove(str(p))
            except Exception:
                pass
        if ids:
            qmarks = ",".join(["?"] * len(ids))
            conn.execute(f"DELETE FROM materials WHERE id IN ({qmarks})", ids)
        return len(ids)


# ---------- WEEK SUBMISSIONS (недельные, многофайловые) ----------


def get_or_create_week_submission(student_id: str, week_no: int) -> int:
    """Вернёт id существующей сдачи студента за неделю или создаст новую (status='submitted')."""
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM submissions WHERE student_id=? AND week_no=? ORDER BY id DESC LIMIT 1",
            (student_id, week_no),
        ).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            (
                "INSERT INTO submissions(week_no, student_id, status, created_at_utc) "
                "VALUES(?, ?, 'submitted', strftime('%s','now'))"
            ),
            (week_no, student_id),
        )
        return cur.lastrowid


def add_submission_file(
    submission_id: int,
    sha256: str,
    size_bytes: int,
    path: str,
    mime: Optional[str],
) -> int:
    """
    Добавит файл в недельную сдачу.
    Возвращает file_id, а при дубликате (тот же sha256+size, не удалённый) — существующий id.
    """
    with db() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO week_submission_files(submission_id, sha256, size_bytes, path, mime, created_at_utc)
                VALUES(?,?,?,?,?, strftime('%s','now'))
                """,
                (submission_id, sha256, size_bytes, path, mime),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Дубликат (UNIQUE по submission_id + sha256 + size_bytes для не удалённых)
            return -1


def list_submission_files(student_id: str, week_no: int) -> List[Dict]:
    """Файлы сдачи студента за неделю (только не удалённые)."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.sha256, f.size_bytes, f.path, f.mime, f.created_at_utc
            FROM submissions s
            JOIN week_submission_files f ON f.submission_id = s.id
            WHERE s.student_id=? AND s.week_no=? AND f.deleted_at_utc IS NULL
            ORDER BY f.id ASC
            """,
            (student_id, week_no),
        ).fetchall()
    return [
        {
            "id": r[0],
            "sha256": r[1],
            "size_bytes": r[2],
            "path": r[3],
            "mime": r[4],
            "created_at_utc": r[5],
        }
        for r in rows
    ]


def soft_delete_submission_file(file_id: int, student_id: str) -> bool:
    """Мягкое удаление файла сдачи (проверяется, что файл принадлежит сдаче данного студента)."""
    with db() as conn:
        row = conn.execute(
            """
            SELECT f.id
            FROM week_submission_files f
            JOIN submissions s ON s.id = f.submission_id
            WHERE f.id=? AND s.student_id=? AND f.deleted_at_utc IS NULL
            """,
            (file_id, student_id),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE week_submission_files SET deleted_at_utc=strftime('%s','now') WHERE id=?",
            (file_id,),
        )
        return True


def list_student_weeks(student_id: str, limit: int = 20) -> List[Tuple[int, int]]:
    """
    Возвращает список (week_no, files_count) по неделям, где у студента есть сдачи.
    """
    with db() as conn:
        rows = conn.execute(
            """
            SELECT s.week_no, COUNT(f.id) AS cnt
            FROM submissions s
            LEFT JOIN week_submission_files f ON f.submission_id=s.id AND f.deleted_at_utc IS NULL
            WHERE s.student_id=? AND s.week_no IS NOT NULL
            GROUP BY s.week_no
            ORDER BY s.week_no DESC
            LIMIT ?
            """,
            (student_id, limit),
        ).fetchall()
    return [(int(r[0]), int(r[1])) for r in rows]


# ---------- WEEKS (для клавиатур) ----------


def list_weeks(limit: int = 50) -> List[int]:
    """Возвращает список номеров недель из таблицы weeks в порядке возрастания."""
    with db() as conn:
        rows = conn.execute(
            "SELECT week_no FROM weeks ORDER BY week_no ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [int(r[0]) for r in rows]


# ---------- TEACHER VIEW (read-only) ----------


def list_students_with_submissions_by_week(week_no: int) -> List[Dict]:
    """
    Возвращает список студентов, у которых есть активные (не удалённые) файлы в сдачах за неделю.
    Формат элементов: { student_id, tg_id, name, files_count }
    Порядок детерминированный: по LOWER(name), затем по u.id.
    """
    with db() as conn:
        rows = conn.execute(
            """
            SELECT s.student_id,
                   u.tg_id,
                   u.name,
                   COUNT(f.id) AS files_count
            FROM submissions s
            JOIN users u ON u.id = s.student_id
            JOIN week_submission_files f
                 ON f.submission_id = s.id AND f.deleted_at_utc IS NULL
            WHERE s.week_no = ?
            GROUP BY s.student_id, u.tg_id, u.name
            ORDER BY LOWER(COALESCE(u.name, '')) ASC, u.id ASC
            """,
            (week_no,),
        ).fetchall()
    return [
        {
            "student_id": r[0],
            "tg_id": r[1],
            "name": r[2],
            "files_count": int(r[3]),
        }
        for r in rows
    ]


def list_week_submission_files_for_teacher(student_id: str, week_no: int) -> List[Dict]:
    """Файлы сдачи студента за неделю (только не удалённые)."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.sha256, f.size_bytes, f.path, f.mime, f.created_at_utc
            FROM submissions s
            JOIN week_submission_files f ON f.submission_id = s.id
            WHERE s.student_id=? AND s.week_no=? AND f.deleted_at_utc IS NULL
            ORDER BY f.id ASC
            """,
            (student_id, week_no),
        ).fetchall()
    return [
        {
            "id": int(r[0]),
            "sha256": r[1],
            "size_bytes": int(r[2]),
            "path": r[3],
            "mime": r[4],
            "created_at_utc": int(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ]
