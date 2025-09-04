from __future__ import annotations

import os
import time
from typing import Optional


def _read_ts(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int((f.read() or "0").strip())
    except Exception:
        return None


def backup_recent(now: Optional[int] = None) -> bool:
    """
    Политика свежести бэкапа согласно L3_Common §5:
    - full backup не старше 24 часов
    - incremental backup не старше 60 минут

    Реализация-заглушка: читает метки времени из var/backup/{recent_full.ts,recent_incr.ts}.
    При отсутствии файлов — возвращает False.
    """
    base = os.environ.get("APP_VAR_DIR", "var")
    bdir = os.path.join(base, "backup")
    full_ts = _read_ts(os.path.join(bdir, "recent_full.ts"))
    incr_ts = _read_ts(os.path.join(bdir, "recent_incr.ts"))
    if full_ts is None or incr_ts is None:
        return False
    now_i = int(now or time.time())
    full_ok = (now_i - full_ts) <= 24 * 3600
    incr_ok = (now_i - incr_ts) <= 60 * 60
    return bool(full_ok and incr_ok)
