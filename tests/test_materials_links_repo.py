import time

from app.core.repos_epic4 import (
    archive_active,
    get_active_material,
    insert_week_material_link,
    list_material_versions,
)
from app.db.conn import db


def _apply_materials_migrations_all() -> None:
    with open("migrations/005_rewire_materials_weeks.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with db() as c:
        c.executescript(sql)
        c.commit()
    with open("migrations/007_materials_versions.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with db() as c:
        c.executescript(sql)
        c.commit()
    # New scope for checksum uniqueness
    with open("migrations/008_materials_hash_scope.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with db() as c:
        c.executescript(sql)
        c.commit()


def _ensure_owner_and_weeks():
    with db() as conn:
        # owner
        row = conn.execute("SELECT id FROM users WHERE role='owner' LIMIT 1").fetchone()
        if not row:
            now = int(time.time())
            conn.execute(
                (
                    "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) "
                    "VALUES('owner-test','owner','Owner', ?, ?)"
                ),
                (now, now),
            )
        # weeks 1 and 2
        for w in (1, 2):
            r = conn.execute("SELECT id FROM weeks WHERE week_no=?", (w,)).fetchone()
            if not r:
                conn.execute(
                    "INSERT INTO weeks(week_no, title, created_at_utc) VALUES(?,?,?)",
                    (w, f"Week {w}", int(time.time())),
                )
        conn.commit()
        owner_id = conn.execute(
            "SELECT id FROM users WHERE role='owner' ORDER BY created_at_utc LIMIT 1"
        ).fetchone()[0]
        wk1_id = conn.execute("SELECT id FROM weeks WHERE week_no=1").fetchone()[0]
    return owner_id, int(wk1_id)


def test_link_insert_idempotency_and_versioning(db_tmpdir):
    _apply_materials_migrations_all()
    owner_id, wk1_id = _ensure_owner_and_weeks()

    url1 = "https://disk.yandex.ru/i/AAA111"
    url2 = "https://disk.yandex.ru/i/BBB222"

    # v1
    mid1 = insert_week_material_link(1, owner_id, url1, visibility="public", type="v")
    assert mid1 > 0
    mat = get_active_material(wk1_id, "v")
    assert (
        mat and mat.path == url1 and int(mat.version) == 1 and int(mat.is_active) == 1
    )

    # repeat same link → noop (-1)
    mid1b = insert_week_material_link(1, owner_id, url1, visibility="public", type="v")
    assert mid1b == -1

    # new link → v2 active, v1 archived
    mid2 = insert_week_material_link(1, owner_id, url2, visibility="public", type="v")
    assert mid2 > 0 and mid2 != mid1
    mat2 = get_active_material(wk1_id, "v")
    assert (
        mat2
        and mat2.path == url2
        and int(mat2.version) == 2
        and int(mat2.is_active) == 1
    )
    hist = list_material_versions(wk1_id, "v", limit=10)
    assert len(hist) >= 2
    # latest first
    assert int(hist[0].version) >= int(hist[1].version)

    # archive active
    assert archive_active(wk1_id, "v") is True
    assert get_active_material(wk1_id, "v") is None


def test_same_link_allowed_in_other_week(db_tmpdir):
    _apply_materials_migrations_all()
    owner_id, wk1_id = _ensure_owner_and_weeks()
    with db() as conn:
        wk2_id = conn.execute("SELECT id FROM weeks WHERE week_no=2").fetchone()[0]

    url = "https://disk.yandex.ru/i/SAME-LINK"
    assert (
        insert_week_material_link(1, owner_id, url, visibility="public", type="v") > 0
    )
    assert (
        insert_week_material_link(2, owner_id, url, visibility="public", type="v") > 0
    )
    m1 = get_active_material(wk1_id, "v")
    m2 = get_active_material(int(wk2_id), "v")
    assert m1 and m2 and m1.path == url and m2.path == url and m1.week_id != m2.week_id
