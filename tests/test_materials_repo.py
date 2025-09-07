import os
import pathlib

from app.core.files import save_blob
from app.core.repos_epic4 import (
    archive_active,
    delete_archived,
    get_active_material,
    insert_week_material_file,
    list_material_versions,
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
    conn.execute(
        "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) "
        "VALUES('owner-test','owner','Owner', strftime('%s','now'), strftime('%s','now'))"
    )
    return conn.execute("SELECT id FROM users WHERE tg_id='owner-test'").fetchone()[0]


def test_promote_archived_duplicate_to_active(db_tmpdir):
    _apply_materials_migrations()
    with db() as conn:
        _ensure_week(conn, 1)
        uid = _ensure_user(conn)

    data_a = b"A-material"
    data_b = b"B-material"
    a = save_blob(data_a, prefix="materials", suggested_name="a.pdf")
    b = save_blob(data_b, prefix="materials", suggested_name="b.pdf")

    # v1: A active
    mid_a1 = insert_week_material_file(
        1,
        uid,
        a.path,
        a.sha256,
        a.size_bytes,
        "application/pdf",
        "public",
        "p",
        "a.pdf",
    )
    assert mid_a1 > 0
    with db() as conn:
        wk_id = conn.execute("SELECT id FROM weeks WHERE week_no=1").fetchone()[0]
    mat = get_active_material(wk_id, "p")
    assert (
        mat and os.path.basename(mat.path).endswith("a.pdf") and int(mat.version) == 1
    )

    # v2: B active, A archived
    mid_b = insert_week_material_file(
        1,
        uid,
        b.path,
        b.sha256,
        b.size_bytes,
        "application/pdf",
        "public",
        "p",
        "b.pdf",
    )
    assert mid_b > 0 and mid_b != mid_a1
    mat = get_active_material(wk_id, "p")
    assert (
        mat and os.path.basename(mat.path).endswith("b.pdf") and int(mat.version) == 2
    )

    # v3: upload A again ⇒ promote archived A to active with v3
    mid_a2 = insert_week_material_file(
        1,
        uid,
        a.path,
        a.sha256,
        a.size_bytes,
        "application/pdf",
        "public",
        "p",
        "a.pdf",
    )
    assert mid_a2 == mid_a1  # same row reused
    mat = get_active_material(wk_id, "p")
    assert (
        mat and os.path.basename(mat.path).endswith("a.pdf") and int(mat.version) == 3
    )


def test_duplicate_in_other_week_or_type_is_rejected(db_tmpdir):
    _apply_materials_migrations()
    with db() as conn:
        _ensure_week(conn, 1)
        _ensure_week(conn, 2)
        uid = _ensure_user(conn)

    data = b"Same-content"
    s = save_blob(data, prefix="materials", suggested_name="c.pdf")
    assert (
        insert_week_material_file(
            1,
            uid,
            s.path,
            s.sha256,
            s.size_bytes,
            "application/pdf",
            "public",
            "p",
            "c.pdf",
        )
        > 0
    )
    # Same content in another week → rejected due to global unique index
    assert (
        insert_week_material_file(
            2,
            uid,
            s.path,
            s.sha256,
            s.size_bytes,
            "application/pdf",
            "public",
            "p",
            "c.pdf",
        )
        == -1
    )
    # Same content in same week but other type → rejected
    assert (
        insert_week_material_file(
            1,
            uid,
            s.path,
            s.sha256,
            s.size_bytes,
            "application/pdf",
            "public",
            "n",
            "c.pdf",
        )
        == -1
    )


def test_archive_active_and_versions_listing(db_tmpdir):
    _apply_materials_migrations()
    with db() as conn:
        _ensure_week(conn, 3)
        uid = _ensure_user(conn)
        wk_id = conn.execute("SELECT id FROM weeks WHERE week_no=3").fetchone()[0]

    x = save_blob(b"X", prefix="materials", suggested_name="x.pdf")
    y = save_blob(b"Y", prefix="materials", suggested_name="y.pdf")
    assert (
        insert_week_material_file(
            3,
            uid,
            x.path,
            x.sha256,
            x.size_bytes,
            "application/pdf",
            "public",
            "p",
            "x.pdf",
        )
        > 0
    )
    # archive active v1
    assert archive_active(wk_id, "p") is True
    # archive when none active
    assert archive_active(wk_id, "p") is False
    # upload new active v2
    assert (
        insert_week_material_file(
            3,
            uid,
            y.path,
            y.sha256,
            y.size_bytes,
            "application/pdf",
            "public",
            "p",
            "y.pdf",
        )
        > 0
    )
    items = list_material_versions(wk_id, "p", limit=10)
    assert len(items) >= 2
    # first row is latest version (v2, active)
    assert int(items[0].version) >= int(items[1].version)
    assert int(items[0].is_active) == 1


def test_delete_archived_and_enforce_limit(db_tmpdir):
    _apply_materials_migrations()
    with db() as conn:
        _ensure_week(conn, 4)
        uid = _ensure_user(conn)
        wk_id = conn.execute("SELECT id FROM weeks WHERE week_no=4").fetchone()[0]

    # Create 1..5 versions
    for i in range(5):
        blob = save_blob(
            f"file-{i}".encode(), prefix="materials", suggested_name=f"f{i}.pdf"
        )
        assert (
            insert_week_material_file(
                4,
                uid,
                blob.path,
                blob.sha256,
                blob.size_bytes,
                "application/pdf",
                "public",
                "p",
                f"f{i}.pdf",
            )
            > 0
        )
    # Enforce limit 3 → should delete oldest archived, keep latest active
    from app.core.repos_epic4 import enforce_archive_limit

    removed = enforce_archive_limit(wk_id, "p", max_versions=3)
    # At least 2 should be removed (5 total - 3 limit)
    assert removed == 2
    # Now delete whatever archived remains
    deleted = delete_archived(wk_id, "p")
    # After enforcing to 3, there should be at most 2 archived to delete
    assert deleted <= 2


def _apply_materials_migrations() -> None:
    from app.db.conn import db as _db

    for m in [
        "migrations/005_rewire_materials_weeks.sql",
        "migrations/007_materials_versions.sql",
    ]:
        p = pathlib.Path(m)
        if not p.exists():
            continue
        sql = p.read_text(encoding="utf-8")
        with _db() as c:
            c.executescript(sql)
            c.commit()
