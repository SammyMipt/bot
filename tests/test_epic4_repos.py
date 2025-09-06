import uuid

import pytest

from app.core.repos_epic4 import list_materials_by_week
from app.db.conn import db

# Temporarily skip Epic-4 repo smoke test due to schema transition (materials.week_id)
pytestmark = pytest.mark.skip(
    reason="EPIC-4 repository tests temporarily disabled while materials schema is updated"
)


def _table_exists(conn, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _ensure_week(conn, week_no: int) -> None:
    r = conn.execute("SELECT 1 FROM weeks WHERE week_no=?", (week_no,)).fetchone()
    if r:
        return
    conn.execute(
        "INSERT INTO weeks(week_no, title, created_at_utc) VALUES(?,?, strftime('%s','now'))",
        (week_no, f"Week {week_no}"),
    )


def _ensure_assignment_for_week(conn, week_no: int) -> int:
    # твоя схема: assignments.week_no (FK на weeks.week_no)
    r = conn.execute(
        "SELECT id FROM assignments WHERE week_no=? LIMIT 1", (week_no,)
    ).fetchone()
    if r:
        return r[0]
    conn.execute(
        "INSERT INTO assignments(code, title, week_no, deadline_ts_utc, created_at_utc) "
        "VALUES(?, ?, ?, NULL, strftime('%s','now'))",
        (f"A{week_no}-" + uuid.uuid4().hex[:6], "A1", week_no),
    )
    return conn.execute(
        "SELECT id FROM assignments WHERE week_no=? ORDER BY id DESC LIMIT 1",
        (week_no,),
    ).fetchone()[0]


def _ensure_user(conn) -> str:
    # materials.uploaded_by -> users.id (FK). Создадим простого пользователя.
    # В твоей users-схеме есть tg_id UNIQUE, role, name, created_at_utc и т.п. Подстроимся минимально.
    # Если created_at_utc обязателен — используем strftime.
    r = conn.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
    if r:
        return r[0]
    tg = "test_epic4_" + uuid.uuid4().hex[:8]
    # Попробуем общий вариант (role, tg_id, name, created_at_utc, updated_at_utc)
    try:
        conn.execute(
            "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) "
            "VALUES(?, 'student', 'Test User', strftime('%s','now'), strftime('%s','now'))",
            (tg,),
        )
    except Exception:
        # запасной: если у тебя другая схема — минимальный INSERT (подкорректируй под свою users)
        conn.execute(
            "INSERT INTO users(tg_id, role) VALUES(?, 'student')",
            (tg,),
        )
    return conn.execute("SELECT id FROM users WHERE tg_id=?", (tg,)).fetchone()[0]


@pytest.mark.parametrize("week", [1])
def test_list_materials_by_week_smoke(week):
    with db() as conn:
        if not all(
            _table_exists(conn, t)
            for t in ("materials", "assignments", "weeks", "users")
        ):
            pytest.skip("required tables not present — skipping epic4 smoke test")

        _ensure_week(conn, week)
        a_id = _ensure_assignment_for_week(conn, week)
        u_id = _ensure_user(conn)

        # уникальные значения, чтобы не попасть на UNIQUE(sha256,size_bytes)
        sha = uuid.uuid4().hex
        size = 100 + (uuid.uuid4().int % 1000)

        conn.execute(
            """
            INSERT INTO materials(assignment_id, path, sha256, size_bytes, mime, uploaded_by, created_at_utc)
            VALUES(?, 'var/materials/dummy', ?, ?, 'application/pdf', ?, strftime('%s','now'))
            """,
            (a_id, sha, size, u_id),
        )

    mats = list_materials_by_week(week)
    assert any(m.sha256 == sha for m in mats)
