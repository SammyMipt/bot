import time

import pytest

from app.core.course_imports import (
    apply_assignments,
    apply_grades,
    preview_assignments,
    preview_grades,
)
from app.db.conn import db

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _apply_migrations():
    import app.db.conn as conn

    paths = [
        "migrations/002_epic5_users_assignments.sql",
        "migrations/006_fix_tsa_types.sql",
        "migrations/014_grades.sql",
    ]
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.db() as c:
            c.executescript(sql)
            c.commit()


def _seed_basic_users():
    ts = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO users(id, tg_id, role, name, email, group_name, is_active, created_at_utc, updated_at_utc) "
            "VALUES(?,?,?,?,?,?,1,?,?)",
            (
                "stu-1",
                "s-1",
                "student",
                "Student One",
                "student@example.com",
                "A",
                ts,
                ts,
            ),
        )
        conn.execute(
            "INSERT INTO users(id, tg_id, role, name, email, capacity, is_active, created_at_utc, updated_at_utc) "
            "VALUES(?,?,?,?,?,?,1,?,?)",
            (
                "tea-1",
                "t-1",
                "teacher",
                "Teacher One",
                "teacher@example.com",
                5,
                ts,
                ts,
            ),
        )
        conn.execute(
            "INSERT INTO users(id, tg_id, role, name, email, capacity, is_active, created_at_utc, updated_at_utc) "
            "VALUES(?,?,?,?,?,?,1,?,?)",
            (
                "tea-2",
                "t-2",
                "teacher",
                "Teacher Two",
                "teacher2@example.com",
                5,
                ts,
                ts,
            ),
        )
        conn.commit()


def _seed_weeks(*week_numbers: int):
    ts = int(time.time())
    with db() as conn:
        for w in week_numbers:
            conn.execute(
                "INSERT OR REPLACE INTO weeks(week_no, title, created_at_utc) VALUES(?,?,?)",
                (w, f"Week {w}", ts),
            )
        conn.commit()


def _seed_assignment(student_id: str, week_no: int, teacher_id: str):
    ts = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO teacher_student_assignments(week_no, teacher_id, student_id, created_at_utc) "
            "VALUES(?,?,?,?)",
            (week_no, teacher_id, student_id, ts),
        )
        conn.commit()


def test_preview_assignments_classifies_rows():
    _apply_migrations()
    _seed_basic_users()
    _seed_weeks(1, 2)
    _seed_assignment("stu-1", 1, "tea-1")

    csv_content = (
        "student_email,week,teacher_email\n"
        "student@example.com,1,teacher2@example.com\n"
        "student@example.com,2,teacher2@example.com\n"
        "student@example.com,99,teacher2@example.com\n"
        " , , \n"
    ).encode("utf-8")

    preview = preview_assignments(csv_content)
    statuses = [row.status for row in preview.rows]
    assert statuses == ["update", "new", "error", "skip"]

    summary = preview.summary()
    assert summary["total"] == 4
    assert summary["new"] == 1
    assert summary["update"] == 1
    assert summary["error"] == 1
    assert summary["skip"] == 1


def test_apply_assignments_upserts_and_audits():
    _apply_migrations()
    _seed_basic_users()
    _seed_weeks(1, 2)
    _seed_assignment("stu-1", 1, "tea-1")

    csv_content = (
        "student_email,week,teacher_email\n"
        "student@example.com,1,teacher2@example.com\n"
        "student@example.com,2,teacher2@example.com\n"
    ).encode("utf-8")
    preview = preview_assignments(csv_content)
    result = apply_assignments(preview)

    assert result.applied == 2
    assert result.errors == 0
    assert result.unchanged == 0

    with db() as conn:
        row1 = conn.execute(
            "SELECT teacher_id FROM teacher_student_assignments WHERE student_id=? AND week_no=?",
            ("stu-1", 1),
        ).fetchone()
        row2 = conn.execute(
            "SELECT teacher_id FROM teacher_student_assignments WHERE student_id=? AND week_no=?",
            ("stu-1", 2),
        ).fetchone()
    assert row1 and row1[0] == "tea-2"
    assert row2 and row2[0] == "tea-2"


def test_preview_grades_statuses_and_errors():
    _apply_migrations()
    _seed_basic_users()
    _seed_weeks(1, 2, 3)
    _seed_assignment("stu-1", 1, "tea-2")
    _seed_assignment("stu-1", 2, "tea-2")
    _seed_assignment("stu-1", 3, "tea-2")

    from app.core.repos_epic4 import set_week_grade

    set_week_grade("stu-1", 1, "tea-2", 7)
    set_week_grade("stu-1", 1, "tea-2", 7)
    set_week_grade("stu-1", 3, "tea-2", 10)

    csv_content = (
        "student_email,week,grade,teacher_email\n"
        "student@example.com,1,9,teacher2@example.com\n"
        "student@example.com,2,8,teacher2@example.com\n"
        "student@example.com,3,10,teacher2@example.com\n"
        "student@example.com,4,8,teacher2@example.com\n"
    ).encode("utf-8")

    preview = preview_grades(csv_content)
    statuses = [row.status for row in preview.rows]
    assert statuses == ["update", "new", "unchanged", "error"]

    messages = [row.message for row in preview.rows]
    assert "неделя отсутствует" in messages[-1]


def test_apply_grades_updates_submissions_and_history():
    _apply_migrations()
    _seed_basic_users()
    _seed_weeks(1, 2, 3)
    _seed_assignment("stu-1", 1, "tea-2")
    _seed_assignment("stu-1", 2, "tea-2")
    _seed_assignment("stu-1", 3, "tea-2")

    from app.core.repos_epic4 import set_week_grade

    set_week_grade("stu-1", 3, "tea-2", 10)

    csv_content = (
        "student_email,week,grade,teacher_email\n"
        "student@example.com,1,9,teacher2@example.com\n"
        "student@example.com,2,8,teacher2@example.com\n"
        "student@example.com,3,10,teacher2@example.com\n"
    ).encode("utf-8")

    preview = preview_grades(csv_content)
    result = apply_grades(preview)

    assert result.applied == 2
    assert result.unchanged == 1
    assert result.errors == 0

    with db() as conn:
        row1 = conn.execute(
            "SELECT grade FROM submissions WHERE student_id=? AND week_no=? ORDER BY id DESC LIMIT 1",
            ("stu-1", 1),
        ).fetchone()
        row2 = conn.execute(
            "SELECT grade FROM submissions WHERE student_id=? AND week_no=? ORDER BY id DESC LIMIT 1",
            ("stu-1", 2),
        ).fetchone()
        row3 = conn.execute(
            "SELECT grade FROM submissions WHERE student_id=? AND week_no=? ORDER BY id DESC LIMIT 1",
            ("stu-1", 3),
        ).fetchone()
        grades_history = conn.execute(
            "SELECT week_no, score_int, origin FROM grades ORDER BY id"
        ).fetchall()
    assert row1 and row1[0] == "9"
    assert row2 and row2[0] == "8"
    assert row3 and row3[0] == "10"
    assert [g[2] for g in grades_history][-2:] == ["owner_import", "owner_import"]
