import time
import uuid

from app.core.files import save_blob
from app.core.repos_epic4 import (
    add_submission_file,
    get_or_create_week_submission,
    insert_week_material_file,
    list_materials_by_week,
    list_submission_files,
    list_weeks,
    soft_delete_submission_file,
)
from app.db.conn import db


def _ensure_week(conn, week_no: int) -> None:
    r = conn.execute("SELECT 1 FROM weeks WHERE week_no=?", (week_no,)).fetchone()
    if not r:
        conn.execute(
            "INSERT INTO weeks(week_no, title, created_at_utc) VALUES(?,?, strftime('%s','now'))",
            (week_no, f"Week {week_no}"),
        )


def _ensure_user(conn) -> str:
    r = conn.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
    if r:
        return r[0]
    tg = "test_epic4_" + uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) "
        "VALUES(?, 'student', 'Test User', strftime('%s','now'), strftime('%s','now'))",
        (tg,),
    )
    return conn.execute("SELECT id FROM users WHERE tg_id=?", (tg,)).fetchone()[0]


def test_materials_visibility_and_dedup(db_tmpdir):
    week = 1
    with db() as conn:
        _ensure_week(conn, week)
        uploader_id = _ensure_user(conn)
    # имитируем два разных бинарника
    data_pub = b"public material " + str(time.time()).encode()
    data_tch = b"teacher material " + str(time.time()).encode()

    saved_pub = save_blob(data_pub, prefix="materials", suggested_name="pub.pdf")
    saved_tch = save_blob(data_tch, prefix="materials", suggested_name="tch.pdf")

    mid1 = insert_week_material_file(
        week_no=week,
        uploaded_by=uploader_id,
        path=saved_pub.path,
        sha256=saved_pub.sha256,
        size_bytes=saved_pub.size_bytes,
        mime="application/pdf",
        visibility="public",
    )
    assert mid1 > 0

    # Повторная вставка того же файла — должна вернуть -1
    mid1_dup = insert_week_material_file(
        week_no=week,
        uploaded_by=uploader_id,
        path=saved_pub.path,
        sha256=saved_pub.sha256,
        size_bytes=saved_pub.size_bytes,
        mime="application/pdf",
        visibility="public",
    )
    assert mid1_dup == -1

    mid2 = insert_week_material_file(
        week_no=week,
        uploaded_by=uploader_id,
        path=saved_tch.path,
        sha256=saved_tch.sha256,
        size_bytes=saved_tch.size_bytes,
        mime="application/pdf",
        visibility="teacher_only",
    )
    assert mid2 > 0

    # Студент видит только public
    mats_student = list_materials_by_week(week, audience="student")
    assert all(m.visibility == "public" for m in mats_student)
    assert any(m.id == mid1 for m in mats_student)
    assert all(m.id != mid2 for m in mats_student)

    # Преподаватель/owner видит обе записи
    mats_teacher = list_materials_by_week(week, audience="teacher")
    ids = {m.id for m in mats_teacher}
    assert mid1 in ids and mid2 in ids


def test_submission_add_list_delete(db_tmpdir):
    week = 2
    with db() as conn:
        _ensure_week(conn, week)
        student_id = _ensure_user(conn)

    sub_id = get_or_create_week_submission(student_id, week)
    assert sub_id > 0

    data = b"answer v1"
    saved = save_blob(data, prefix="submissions", suggested_name="ans1.pdf")

    fid1 = add_submission_file(
        submission_id=sub_id,
        sha256=saved.sha256,
        size_bytes=saved.size_bytes,
        path=saved.path,
        mime="application/pdf",
    )
    assert fid1 > 0

    # Дубликат того же файла
    fid_dup = add_submission_file(
        submission_id=sub_id,
        sha256=saved.sha256,
        size_bytes=saved.size_bytes,
        path=saved.path,
        mime="application/pdf",
    )
    assert fid_dup == -1 or fid_dup == fid1  # в нашей реализации возвращаем -1

    files = list_submission_files(student_id, week)
    assert len(files) == 1
    assert files[0]["id"] == fid1

    # Мягкое удаление
    ok = soft_delete_submission_file(fid1, student_id)
    assert ok is True
    files_after = list_submission_files(student_id, week)
    assert files_after == []


def test_list_weeks_sorted(db_tmpdir):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(1,'W1', strftime('%s','now'))"
        )
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(3,'W3', strftime('%s','now'))"
        )
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(2,'W2', strftime('%s','now'))"
        )
    weeks = list_weeks(limit=10)
    assert weeks[:3] == [1, 2, 3]
