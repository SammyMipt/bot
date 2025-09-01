import uuid

import pytest

from app.core.files import save_blob
from app.core.repos_epic4 import (
    add_submission_file,
    get_or_create_week_submission,
    list_students_with_submissions_by_week,
    list_week_submission_files_for_teacher,
    soft_delete_submission_file,
)
from app.db.conn import db


def _ensure_week(conn, week_no: int) -> None:
    r = conn.execute("SELECT 1 FROM weeks WHERE week_no=?", (week_no,)).fetchone()
    if r:
        return
    conn.execute(
        "INSERT INTO weeks(week_no, title, created_at_utc) VALUES(?,?, strftime('%s','now'))",
        (week_no, f"Week {week_no}"),
    )


def _create_user(conn, name: str, role: str = "student") -> int:
    tg = f"tg_{name.lower()}_" + uuid.uuid4().hex[:6]
    conn.execute(
        "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) "
        "VALUES(?, ?, ?, strftime('%s','now'), strftime('%s','now'))",
        (tg, role, name),
    )
    return int(
        conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()[0]
    )


@pytest.mark.usefixtures("db_tmpdir")
def test_list_students_with_submissions_by_week_counts_and_order():
    week = 5
    with db() as conn:
        _ensure_week(conn, week)
        alice_id = _create_user(conn, "Alice")
        bob_id = _create_user(conn, "Bob")
        charlie_id = _create_user(conn, "Charlie")

    # Alice: 1 file
    sub_a = get_or_create_week_submission(alice_id, week)
    saved_a = save_blob(b"a1", prefix="submissions", suggested_name="a1.txt")
    _ = add_submission_file(
        submission_id=sub_a,
        sha256=saved_a.sha256,
        size_bytes=saved_a.size_bytes,
        path=saved_a.path,
        mime="text/plain",
    )

    # Bob: 2 files (1 soft-deleted, 1 alive)
    sub_b = get_or_create_week_submission(bob_id, week)
    saved_b1 = save_blob(b"b1", prefix="submissions", suggested_name="b1.txt")
    saved_b2 = save_blob(b"b2", prefix="submissions", suggested_name="b2.txt")
    _ = add_submission_file(
        submission_id=sub_b,
        sha256=saved_b1.sha256,
        size_bytes=saved_b1.size_bytes,
        path=saved_b1.path,
        mime="text/plain",
    )
    fid_b2 = add_submission_file(
        submission_id=sub_b,
        sha256=saved_b2.sha256,
        size_bytes=saved_b2.size_bytes,
        path=saved_b2.path,
        mime="text/plain",
    )
    assert fid_b2 > 0
    # soft-delete second file; only one alive should remain
    assert soft_delete_submission_file(fid_b2, bob_id)

    # Charlie: 0 files -> should not appear
    _ = get_or_create_week_submission(charlie_id, week)

    students = list_students_with_submissions_by_week(week)
    # Expect Alice (1), Bob (1 alive), Charlie excluded
    names = [s.get("name") for s in students]
    assert "Alice" in names and "Bob" in names
    assert "Charlie" not in names
    by_name = {s["name"]: s for s in students}
    assert by_name["Alice"]["files_count"] == 1
    assert by_name["Bob"]["files_count"] == 1
    # Order: alphabetical by name (Alice before Bob)
    assert names.index("Alice") < names.index("Bob")


@pytest.mark.usefixtures("db_tmpdir")
def test_list_week_submission_files_for_teacher_filters_deleted():
    week = 6
    with db() as conn:
        _ensure_week(conn, week)
        sid = _create_user(conn, "Dora")

    sub_id = get_or_create_week_submission(sid, week)
    s1 = save_blob(b"x1", prefix="submissions", suggested_name="x1.bin")
    s2 = save_blob(b"x2", prefix="submissions", suggested_name="x2.bin")
    with db() as conn:
        conn.execute(
            "INSERT INTO week_submission_files(submission_id, sha256, size_bytes, path, mime, created_at_utc) "
            "VALUES(?,?,?,?,?, strftime('%s','now'))",
            (sub_id, s1.sha256, s1.size_bytes, s1.path, "application/octet-stream"),
        )
        conn.execute(
            "INSERT INTO week_submission_files(submission_id, sha256, size_bytes, path, mime, created_at_utc) "
            "VALUES(?,?,?,?,?, strftime('%s','now'))",
            (sub_id, s2.sha256, s2.size_bytes, s2.path, "application/octet-stream"),
        )
        fid2 = int(
            conn.execute(
                "SELECT id FROM week_submission_files WHERE submission_id=? AND sha256=?",
                (sub_id, s2.sha256),
            ).fetchone()[0]
        )
        conn.execute(
            "UPDATE week_submission_files SET deleted_at_utc=strftime('%s','now') WHERE id=?",
            (fid2,),
        )

    files = list_week_submission_files_for_teacher(sid, week)
    assert len(files) == 1
    f = files[0]
    assert set(f.keys()) == {
        "id",
        "sha256",
        "size_bytes",
        "path",
        "mime",
        "created_at_utc",
    }
    assert f["sha256"] == s1.sha256 and f["size_bytes"] == s1.size_bytes
    assert f["size_bytes"] > 0

    # now soft delete the only alive file and expect empty list
    assert soft_delete_submission_file(f["id"], sid)
    files_after = list_week_submission_files_for_teacher(sid, week)
    assert files_after == []
