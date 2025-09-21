import time

import pytest


def _apply_migrations():
    import app.db.conn as conn

    # Run migrations idempotently where needed for booking feature
    for path in (
        "migrations/002_epic5_users_assignments.sql",
        "migrations/004_course_weeks_schema.sql",
        "migrations/009_slots_location.sql",
        "migrations/013_slot_enrollments_week.sql",
    ):
        try:
            with open(path, "r", encoding="utf-8") as f:
                sql = f.read()
            with conn.db() as c:
                try:
                    c.executescript(sql)
                    c.commit()
                except Exception:
                    pass
        except FileNotFoundError:
            pass


def _mk_user(tg_id: str, role: str, name: str | None = None) -> str:
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES(?,?,?,?,?)",
            (tg_id, role, name or tg_id, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return row[0]


def _mk_week(week_no: int, *, deadline_ts_utc: int | None = None):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(?,?,?)",
            (week_no, f"W{week_no}", now),
        )
        if deadline_ts_utc is not None:
            conn.execute(
                "UPDATE weeks SET deadline_ts_utc=? WHERE week_no=?",
                (deadline_ts_utc, week_no),
            )
        conn.commit()


def _assign_teacher(week_no: int, teacher_id: str, student_id: str):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO teacher_student_assignments(week_no, teacher_id, student_id, created_at_utc) VALUES(?,?,?,?)",
            (week_no, teacher_id, student_id, now),
        )
        conn.commit()


def _mk_slot(
    created_by: str, starts_at_utc: int, duration: int, cap: int, status: str = "open"
) -> int:
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO slots(starts_at_utc, duration_min, capacity, status, created_by, created_at_utc) VALUES(?,?,?,?,?,?)",
            (starts_at_utc, duration, cap, status, created_by, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def _enroll(slot_id: int, user_id: str):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO slot_enrollments(slot_id, user_id, status, booked_at_utc) VALUES(?,?, 'booked', ?)",
            (slot_id, user_id, now),
        )
        conn.commit()


def test_repo_booking_happy_path_and_week_enforcement(monkeypatch, db_tmpdir):
    from app.core.bookings_repo import (
        book_slot_for_week,
        list_active_bookings,
        list_available_slots_for_week,
    )

    _apply_migrations()
    teacher = _mk_user("t1", "teacher", "Teacher One")
    student = _mk_user("s1", "student", "Student One")
    future = int(time.time()) + 3600
    _mk_week(3, deadline_ts_utc=future + 7200)
    _assign_teacher(3, teacher, student)
    sid = _mk_slot(teacher, future, 30, 2, status="open")

    slots = list_available_slots_for_week(student, 3)
    assert any(s.id == sid for s in slots)

    bid = book_slot_for_week(student, 3, sid)
    assert isinstance(bid, int)
    # idempotent when same slot booked again
    bid2 = book_slot_for_week(student, 3, sid)
    assert bid2 == bid

    active = list_active_bookings(student)
    assert any(w == 3 and s == sid for w, s, *_ in active)


def test_repo_booking_errors(monkeypatch, db_tmpdir):
    from app.core.bookings_repo import BookingError, book_slot_for_week

    _apply_migrations()
    teacher = _mk_user("t2", "teacher", "Teacher Two")
    student = _mk_user("s2", "student", "Student Two")

    # 1) Not assigned
    _mk_week(4, deadline_ts_utc=int(time.time()) + 7200)
    sid = _mk_slot(teacher, int(time.time()) + 3600, 30, 1, status="open")
    with pytest.raises(BookingError) as e1:
        book_slot_for_week(student, 4, sid)
    assert e1.value.code == "E_NOT_ASSIGNED"

    # 2) Past deadline
    _mk_week(5, deadline_ts_utc=int(time.time()) - 100)
    _assign_teacher(5, teacher, student)
    sid2 = _mk_slot(teacher, int(time.time()) + 7200, 30, 1, status="open")
    with pytest.raises(BookingError) as e2:
        book_slot_for_week(student, 5, sid2)
    assert e2.value.code == "E_PAST_DEADLINE"

    # 3) Slot closed
    _mk_week(6, deadline_ts_utc=int(time.time()) + 7200)
    _assign_teacher(6, teacher, student)
    sid3 = _mk_slot(teacher, int(time.time()) + 7200, 30, 1, status="closed")
    with pytest.raises(BookingError) as e3:
        book_slot_for_week(student, 6, sid3)
    assert e3.value.code == "E_SLOT_CLOSED"

    # 4) Slot full
    _mk_week(7, deadline_ts_utc=int(time.time()) + 7200)
    _assign_teacher(7, teacher, student)
    sid4 = _mk_slot(teacher, int(time.time()) + 7200, 30, 1, status="open")
    other = _mk_user("s3", "student", "Other")
    _enroll(sid4, other)
    with pytest.raises(BookingError) as e4:
        book_slot_for_week(student, 7, sid4)
    assert e4.value.code == "E_SLOT_FULL"

    # 5) Already booked for this week
    _mk_week(8, deadline_ts_utc=int(time.time()) + 7200)
    _assign_teacher(8, teacher, student)
    s_a = _mk_slot(teacher, int(time.time()) + 7200, 30, 2, status="open")
    s_b = _mk_slot(teacher, int(time.time()) + 9000, 30, 2, status="open")
    _ = book_slot_for_week(student, 8, s_a)
    with pytest.raises(BookingError) as e5:
        book_slot_for_week(student, 8, s_b)
    assert e5.value.code == "E_ALREADY_BOOKED"
