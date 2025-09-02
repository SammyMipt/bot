import time

import pytest

from app.db import repo_users
from app.db.conn import db


def _apply_epic5_migration():
    with open("migrations/002_epic5_users_assignments.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with db() as conn:
        conn.executescript(sql)
        conn.commit()


def _insert_user(
    role: str, name: str, email: str | None = None, group_name: str | None = None
):
    now = int(time.time())
    with db() as conn:
        conn.execute(
            (
                "INSERT INTO users(tg_id, role, name, "
                "email, group_name, tef, capacity, is_active, "
                "created_at_utc, updated_at_utc) "
                "VALUES(NULL, ?, ?, ?, ?, NULL, NULL, 1, ?, ?)"
            ),
            (role, name, email, group_name, now, now),
        )


@pytest.mark.usefixtures("db_tmpdir")
def test_teacher_registration_bind_success_and_race():
    _apply_epic5_migration()
    # create two free teachers
    _insert_user("teacher", "Teacher One", email="t1@example.com")
    _insert_user("teacher", "Teacher Two", email="t2@example.com")

    # find free teachers
    cands = repo_users.find_free_teachers_for_bind()
    assert len(cands) >= 2

    uid = cands[0]["id"]
    tg_id = "tg_dev_1001"

    # bind should succeed
    assert repo_users.bind_tg(uid, tg_id) is True
    # binding same tg to another user must fail (race/duplicate tg)
    other = cands[1]["id"]
    assert repo_users.bind_tg(other, tg_id) is False
    # is_tg_bound returns True
    assert repo_users.is_tg_bound(tg_id) is True

    # verify stored in DB
    with db() as conn:
        r = conn.execute("SELECT tg_id FROM users WHERE id=?", (uid,)).fetchone()
        assert r[0] == tg_id


@pytest.mark.usefixtures("db_tmpdir")
def test_student_registration_find_by_email_and_bind():
    _apply_epic5_migration()
    # create students
    _insert_user("student", "Alice A", email="alice@univ.edu", group_name="IU5-11")
    _insert_user("student", "Bob B", email="bob@univ.edu", group_name="IU5-12")

    # success path: exact match (case-insensitive)
    cands = repo_users.find_students_by_email("ALICE@univ.edu")
    assert len(cands) == 1
    uid = cands[0]["id"]
    tg_id = "tg_dev_2001"
    assert repo_users.bind_tg(uid, tg_id) is True
    assert repo_users.is_tg_bound(tg_id) is True

    # not found path
    assert repo_users.find_students_by_email("missing@univ.edu") == []

    # multiple candidates path: create another with same email
    _insert_user("student", "Alice Dup", email="alice@univ.edu", group_name="IU5-13")
    cands_multi = repo_users.find_students_by_email("alice@univ.edu")
    assert len(cands_multi) >= 1
    # already bound one should not appear (ensure only unbound listed)
    ids = {c["id"] for c in cands_multi}
    assert uid not in ids
