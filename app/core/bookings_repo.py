from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Sequence

from app.db.conn import db


class BookingError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass
class SlotInfo:
    id: int
    starts_at_utc: int
    duration_min: int
    capacity: int
    status: str
    booked: int


def _has_col(conn, table: str, col: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return col in cols


def get_assigned_teacher(student_id: str, week_no: int) -> Optional[str]:
    with db() as conn:
        row = conn.execute(
            (
                "SELECT teacher_id FROM teacher_student_assignments "
                "WHERE week_no=? AND student_id=? LIMIT 1"
            ),
            (week_no, student_id),
        ).fetchone()
        return str(row[0]) if row and row[0] is not None else None


def _deadline_utc(week_no: int) -> Optional[int]:
    with db() as conn:
        row = conn.execute(
            "SELECT deadline_ts_utc FROM weeks WHERE week_no=?",
            (week_no,),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None


def _has_final_grade(student_id: str, week_no: int) -> bool:
    # A week is considered graded if there exists a submission marked graded
    # or with non-null grade for this week and student.
    with db() as conn:
        row = conn.execute(
            (
                "SELECT 1 FROM submissions "
                "WHERE student_id=? AND week_no=? "
                "AND (status='graded' OR grade IS NOT NULL) LIMIT 1"
            ),
            (student_id, week_no),
        ).fetchone()
        return bool(row)


def list_available_slots_for_week(student_id: str, week_no: int) -> list[SlotInfo]:
    teacher_id = get_assigned_teacher(student_id, week_no)
    if not teacher_id:
        return []
    now = int(time.time())
    with db() as conn:
        q = (
            "SELECT s.id, s.starts_at_utc, s.duration_min, s.capacity, s.status, "
            "COALESCE(b.cnt,0) as booked "
            "FROM slots s "
            "LEFT JOIN (SELECT slot_id, COUNT(1) as cnt FROM slot_enrollments "
            "           WHERE status='booked' GROUP BY slot_id) b ON b.slot_id = s.id "
            "WHERE s.created_by=? AND s.status='open' AND s.starts_at_utc > ? "
            "ORDER BY s.starts_at_utc ASC"
        )
        rows = conn.execute(q, (teacher_id, now)).fetchall()
    out: list[SlotInfo] = []
    for r in rows:
        sid, st, dur, cap, status, booked = (
            int(r[0]),
            int(r[1]),
            int(r[2]),
            int(r[3]),
            str(r[4]),
            int(r[5]),
        )
        if booked < cap:
            out.append(
                SlotInfo(
                    id=sid,
                    starts_at_utc=st,
                    duration_min=dur,
                    capacity=cap,
                    status=status,
                    booked=booked,
                )
            )
    return out


def book_slot_for_week(student_id: str, week_no: int, slot_id: int) -> int:
    """Create a booking; returns enrollment id.

    Raises BookingError with code in:
    - E_PAST_DEADLINE
    - E_ALREADY_GRADED
    - E_NOT_ASSIGNED
    - E_NOT_FOUND
    - E_SLOT_CLOSED
    - E_SLOT_FULL
    - E_ALREADY_BOOKED
    - E_SCHEMA_MISSING (no week_no support)
    """
    now = int(time.time())
    dl = _deadline_utc(week_no)
    if dl is not None and now > dl:
        raise BookingError("E_PAST_DEADLINE")
    if _has_final_grade(student_id, week_no):
        raise BookingError("E_ALREADY_GRADED")
    with db() as conn:
        # Check schema support
        if not _has_col(conn, "slot_enrollments", "week_no"):
            raise BookingError("E_SCHEMA_MISSING")
        # Get slot and verify it is open, future and belongs to assigned teacher
        teacher_id = get_assigned_teacher(student_id, week_no)
        if not teacher_id:
            raise BookingError("E_NOT_ASSIGNED")
        row = conn.execute(
            (
                "SELECT s.starts_at_utc, s.duration_min, s.capacity, s.status, s.created_by, "
                "COALESCE((SELECT COUNT(1) FROM slot_enrollments e WHERE e.slot_id=s.id AND e.status='booked'),0) "
                "FROM slots s WHERE s.id=?"
            ),
            (slot_id,),
        ).fetchone()
        if not row:
            raise BookingError("E_NOT_FOUND")
        starts_at_utc = int(row[0])
        cap = int(row[2])
        status = str(row[3])
        created_by = str(row[4])
        booked = int(row[5])
        if created_by != teacher_id:
            raise BookingError("E_NOT_FOUND")
        if status != "open":
            raise BookingError("E_SLOT_CLOSED")
        if starts_at_utc <= now:
            raise BookingError("E_SLOT_CLOSED")
        if booked >= cap:
            raise BookingError("E_SLOT_FULL")
        # Enforce single active booking per (student,week)
        exists = conn.execute(
            (
                "SELECT id, slot_id FROM slot_enrollments "
                "WHERE user_id=? AND week_no=? AND status='booked' LIMIT 1"
            ),
            (student_id, week_no),
        ).fetchone()
        if exists:
            # If already booked the same slot — idempotent success (return same id)
            ex_id, ex_slot_id = int(exists[0]), int(exists[1])
            if ex_slot_id == slot_id:
                return ex_id
            raise BookingError("E_ALREADY_BOOKED")
        # If a previous enrollment exists for same (slot,user) with non-booked status, reuse it
        prev = conn.execute(
            "SELECT id, status FROM slot_enrollments WHERE slot_id=? AND user_id=? LIMIT 1",
            (slot_id, student_id),
        ).fetchone()
        if prev:
            pid, pstatus = int(prev[0]), str(prev[1])
            if pstatus == "booked":
                return pid
            conn.execute(
                "UPDATE slot_enrollments SET status='booked', booked_at_utc=?, week_no=? WHERE id=?",
                (now, week_no, pid),
            )
            conn.commit()
            return pid
        # Insert new booking
        cur = conn.execute(
            (
                "INSERT INTO slot_enrollments(slot_id, user_id, status, booked_at_utc, week_no) "
                "VALUES(?, ?, 'booked', ?, ?)"
            ),
            (slot_id, student_id, now, week_no),
        )
        conn.commit()
        return int(cur.lastrowid)


def cancel_week_booking(student_id: str, week_no: int) -> bool:
    with db() as conn:
        if not _has_col(conn, "slot_enrollments", "week_no"):
            raise BookingError("E_SCHEMA_MISSING")
        row = conn.execute(
            (
                "SELECT id FROM slot_enrollments WHERE user_id=? AND week_no=? AND status='booked' LIMIT 1"
            ),
            (student_id, week_no),
        ).fetchone()
        if not row:
            return False
        bid = int(row[0])
        conn.execute(
            "UPDATE slot_enrollments SET status='canceled' WHERE id=?",
            (bid,),
        )
        conn.commit()
        return True


def list_active_bookings(student_id: str) -> Sequence[tuple[int, int, int, str]]:
    """Return (week_no, slot_id, starts_at_utc, teacher_name) for active bookings."""
    with db() as conn:
        rows = conn.execute(
            (
                "SELECT e.week_no, e.slot_id, s.starts_at_utc, COALESCE(u.name, u.tg_id, '') "
                "FROM slot_enrollments e "
                "JOIN slots s ON s.id = e.slot_id "
                "JOIN users u ON u.id = s.created_by "
                "WHERE e.user_id=? AND e.status='booked' "
                "ORDER BY s.starts_at_utc"
            ),
            (student_id,),
        ).fetchall()
        return [(int(r[0] or 0), int(r[1]), int(r[2]), str(r[3] or "")) for r in rows]


def list_history(student_id: str) -> Sequence[tuple[int, int, str, int]]:
    """Return (week_no, slot_id, status, starts_at_utc) for non-active enrollments."""
    with db() as conn:
        rows = conn.execute(
            (
                "SELECT e.week_no, e.slot_id, e.status, s.starts_at_utc "
                "FROM slot_enrollments e JOIN slots s ON s.id=e.slot_id "
                "WHERE e.user_id=? AND e.status!='booked' "
                "ORDER BY s.starts_at_utc DESC"
            ),
            (student_id,),
        ).fetchall()
        return [(int(r[0] or 0), int(r[1]), str(r[2]), int(r[3])) for r in rows]
