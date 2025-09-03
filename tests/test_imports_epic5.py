import csv
import io

import pytest

from app.core.imports_epic5 import (
    STUDENT_HEADERS,
    TEACHER_HEADERS,
    get_templates,
    get_users_summary,
    import_students_csv,
    import_teachers_csv,
)
from app.db.conn import db


def _apply_epic5_migration():
    # Apply 002 migration so that new columns/tables exist in test DB
    with open("migrations/002_epic5_users_assignments.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with db() as conn:
        conn.executescript(sql)
        conn.commit()


def _csv_bytes(headers, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


@pytest.mark.usefixtures("db_tmpdir")
def test_import_teachers_create_and_update():
    _apply_epic5_migration()
    # create
    content = _csv_bytes(
        TEACHER_HEADERS,
        [["Иванов", "Иван", "Иванович", "ivanov@example.com", "2", "10"]],
    )
    res = import_teachers_csv(content)
    assert res.created == 1 and res.updated == 0 and not res.errors

    with db() as conn:
        r = conn.execute(
            "SELECT role, name, email, tef, capacity, is_active, tg_id FROM users WHERE role='teacher' AND email=?",
            ("ivanov@example.com",),
        ).fetchone()
        assert r is not None
        assert r[0] == "teacher"
        assert r[1].startswith("Иванов Иван")
        assert r[2] == "ivanov@example.com"
        assert int(r[3]) == 2 and int(r[4]) == 10 and int(r[5]) == 1
        assert r[6] is None  # tg_id untouched

    # update same teacher (by email)
    content2 = _csv_bytes(
        TEACHER_HEADERS,
        [["Иванов", "Иван", "Иванович", "ivanov@example.com", "3", "12"]],
    )
    res2 = import_teachers_csv(content2)
    assert res2.created == 0 and res2.updated == 1 and not res2.errors
    with db() as conn:
        r2 = conn.execute(
            "SELECT tef, capacity FROM users WHERE role='teacher' AND email=?",
            ("ivanov@example.com",),
        ).fetchone()
        assert int(r2[0]) == 3 and int(r2[1]) == 12


@pytest.mark.usefixtures("db_tmpdir")
def test_import_students_create_and_update():
    _apply_epic5_migration()
    content = _csv_bytes(
        STUDENT_HEADERS,
        [["Петров", "Пётр", "Петрович", "", "IU5-21"]],
    )
    res = import_students_csv(content)
    assert res.created == 1 and res.updated == 0 and not res.errors
    with db() as conn:
        r = conn.execute(
            (
                "SELECT role, name, email, group_name, is_active, tg_id "
                "FROM users WHERE role='student' AND name LIKE 'Петров Пётр%'"
            )
        ).fetchone()
        assert (
            r is not None
            and r[0] == "student"
            and r[2] is None
            and r[3] == "IU5-21"
            and int(r[4]) == 1
            and r[5] is None
        )

    # update by name (no email)
    content2 = _csv_bytes(
        STUDENT_HEADERS,
        [["Петров", "Пётр", "Петрович", "", "IU5-22"]],
    )
    res2 = import_students_csv(content2)
    assert res2.created == 0 and res2.updated == 1 and not res2.errors
    with db() as conn:
        r2 = conn.execute(
            "SELECT group_name FROM users WHERE role='student' AND name LIKE 'Петров Пётр%'",
        ).fetchone()
        assert r2[0] == "IU5-22"


@pytest.mark.usefixtures("db_tmpdir")
def test_import_teachers_validation_errors():
    _apply_epic5_migration()
    # bad headers
    content_bad = _csv_bytes(["bad"], [["x"]])
    res = import_teachers_csv(content_bad)
    assert res.errors and res.errors[0][2] == "E_CSV_BAD_HEADERS"

    # invalid fields
    def run(rows, code):
        content = _csv_bytes(TEACHER_HEADERS, rows)
        r = import_teachers_csv(content)
        assert any(e[2] == code for e in r.errors)

    run([["", "A", "", "a@b.com", "1", "1"]], "E_FIELD_REQUIRED")  # surname
    run([["A", "", "", "a@b.com", "1", "1"]], "E_FIELD_REQUIRED")  # name
    run([["A", "B", "", "bad", "1", "1"]], "E_EMAIL_INVALID")
    run([["A", "B", "", "a@b.com", "0", "1"]], "E_TEF_INVALID")
    run([["A", "B", "", "a@b.com", "1", "0"]], "E_CAPACITY_INVALID")

    # duplicate rows in same CSV by email
    content_dupe = _csv_bytes(
        TEACHER_HEADERS,
        [
            ["A", "B", "", "dupe@example.com", "1", "1"],
            ["A2", "B2", "", "dupe@example.com", "2", "2"],
        ],
    )
    resd = import_teachers_csv(content_dupe)
    assert any(e[2] == "E_DUPLICATE_USER" for e in resd.errors)


@pytest.mark.usefixtures("db_tmpdir")
def test_import_students_validation_errors_and_duplicates():
    _apply_epic5_migration()
    # invalid email
    content = _csv_bytes(STUDENT_HEADERS, [["A", "B", "", "bad", "G1"]])
    res = import_students_csv(content)
    assert any(e[2] == "E_EMAIL_INVALID" for e in res.errors)

    # too long group
    content2 = _csv_bytes(
        STUDENT_HEADERS,
        [["A", "B", "", "", "X" * 200]],
    )
    res2 = import_students_csv(content2)
    assert any(e[2] == "E_GROUP_INVALID" for e in res2.errors)

    # duplicate by name (no email)
    content3 = _csv_bytes(
        STUDENT_HEADERS,
        [
            ["A", "B", "C", "", "G1"],
            ["A", "B", "C", "", "G2"],
        ],
    )
    res3 = import_students_csv(content3)
    assert any(e[2] == "E_DUPLICATE_USER" for e in res3.errors)


@pytest.mark.usefixtures("db_tmpdir")
def test_users_summary_and_templates():
    _apply_epic5_migration()
    # Create via imports
    t = _csv_bytes(TEACHER_HEADERS, [["T", "One", "", "t1@example.com", "1", "5"]])
    s = _csv_bytes(STUDENT_HEADERS, [["S", "One", "", "", "G1"]])
    _ = import_teachers_csv(t)
    _ = import_students_csv(s)
    with db() as conn:
        # set tg_id for teacher
        conn.execute("UPDATE users SET tg_id='tg1' WHERE role='teacher'")
        conn.commit()
    summary = get_users_summary()
    assert summary["teachers_total"] == 1
    assert summary["teachers_no_tg"] == 0
    assert summary["students_total"] == 1
    assert summary["students_no_tg"] == 1

    # templates
    tpls = get_templates()
    assert set(tpls.keys()) == {"teachers.csv", "students.csv"}
    # headers present
    for name, data in tpls.items():
        header_line = data.decode("utf-8").strip()
        if name == "teachers.csv":
            assert header_line == ",".join(TEACHER_HEADERS)
        else:
            assert header_line == ",".join(STUDENT_HEADERS)
