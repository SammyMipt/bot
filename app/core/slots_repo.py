from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Tuple

from app.db.conn import db


@dataclass
class Slot:
    id: int
    starts_at_utc: int
    duration_min: int
    capacity: int
    status: str
    created_by: str
    created_at_utc: int


def _overlaps(conn, created_by: str, start_utc: int, end_utc: int) -> bool:
    row = conn.execute(
        (
            "SELECT 1 FROM slots "
            "WHERE created_by=? AND status IN ('open','closed') "
            "AND starts_at_utc < ? "
            "AND (starts_at_utc + duration_min*60) > ? "
            "LIMIT 1"
        ),
        (created_by, end_utc, start_utc),
    ).fetchone()
    return bool(row)


def generate_timeslots(start_utc: int, end_utc: int, duration_min: int) -> List[int]:
    """Return start timestamps (UTC) for slots within [start_utc, end_utc).

    Ensures each slot fits fully before end_utc.
    """
    if end_utc <= start_utc:
        return []
    step = duration_min * 60
    out: List[int] = []
    t = start_utc
    while t + step <= end_utc:
        out.append(t)
        t += step
    return out


def create_slots_for_range(
    created_by: str,
    start_utc: int,
    end_utc: int,
    duration_min: int,
    capacity: int,
    *,
    mode: str | None = None,
    location: str | None = None,
) -> Tuple[int, int]:
    """Create multiple slots within a range.

    Returns (created_count, skipped_count). Skips overlapping with existing slots.
    """
    now = int(time.time())
    created = 0
    skipped = 0
    with db() as conn:
        # Detect optional columns existence to stay compatible if migration not applied yet
        cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)").fetchall()}
        has_mode = "mode" in cols
        has_location = "location" in cols
        for s in generate_timeslots(start_utc, end_utc, duration_min):
            e = s + duration_min * 60
            if _overlaps(conn, created_by, s, e):
                skipped += 1
                continue
            if has_mode and has_location:
                conn.execute(
                    (
                        "INSERT INTO slots(starts_at_utc, duration_min, capacity, status, created_by, created_at_utc, mode, location) "
                        "VALUES(?, ?, ?, 'open', ?, ?, ?, ?)"
                    ),
                    (s, duration_min, capacity, created_by, now, mode, location),
                )
            elif has_mode:
                conn.execute(
                    (
                        "INSERT INTO slots(starts_at_utc, duration_min, capacity, status, created_by, created_at_utc, mode) "
                        "VALUES(?, ?, ?, 'open', ?, ?, ?)"
                    ),
                    (s, duration_min, capacity, created_by, now, mode),
                )
            else:
                conn.execute(
                    (
                        "INSERT INTO slots(starts_at_utc, duration_min, capacity, status, created_by, created_at_utc) "
                        "VALUES(?, ?, ?, 'open', ?, ?)"
                    ),
                    (s, duration_min, capacity, created_by, now),
                )
            created += 1
        conn.commit()
    return created, skipped
