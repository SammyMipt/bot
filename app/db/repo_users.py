from __future__ import annotations

from typing import Dict, List

from app.db.conn import db


def is_tg_bound(tg_id: str) -> bool:
    with db() as conn:
        r = conn.execute(
            "SELECT 1 FROM users WHERE tg_id=? LIMIT 1", (tg_id,)
        ).fetchone()
        return r is not None


def bind_tg(user_id: str, tg_id: str) -> bool:
    """Bind tg_id to user if user is active and not bound; returns True if success."""
    with db() as conn:
        used = conn.execute("SELECT 1 FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if used:
            return False
        cur = conn.execute(
            (
                "UPDATE users SET tg_id=?, updated_at_utc=strftime('%s','now') "
                "WHERE id=? AND tg_id IS NULL AND is_active=1"
            ),
            (tg_id, user_id),
        )
        conn.commit()
        return cur.rowcount == 1


def find_students_by_email(email: str) -> List[Dict]:
    with db() as conn:
        rows = conn.execute(
            (
                "SELECT id, role, name, email, group_name FROM users "
                "WHERE role='student' AND is_active=1 AND tg_id IS NULL AND LOWER(email)=LOWER(?)"
            ),
            (email.strip(),),
        ).fetchall()
    return [
        {
            "id": r[0],
            "role": r[1],
            "name": r[2],
            "email": r[3],
            "group_name": r[4],
        }
        for r in rows
    ]


def is_student_email_bound(email: str) -> bool:
    """True if there's an active student with this email already bound to a tg_id."""
    with db() as conn:
        r = conn.execute(
            (
                "SELECT 1 FROM users "
                "WHERE role='student' AND is_active=1 AND LOWER(email)=LOWER(?) AND tg_id IS NOT NULL LIMIT 1"
            ),
            (email.strip(),),
        ).fetchone()
        return r is not None


def find_free_teachers_for_bind() -> List[Dict]:
    with db() as conn:
        rows = conn.execute(
            (
                "SELECT id, role, name, email, tef, capacity FROM users "
                "WHERE role='teacher' AND is_active=1 AND tg_id IS NULL ORDER BY LOWER(COALESCE(name,'')) ASC, id ASC"
            )
        ).fetchall()
    return [
        {
            "id": r[0],
            "role": r[1],
            "name": r[2],
            "email": r[3],
            "tef": r[4],
            "capacity": r[5],
        }
        for r in rows
    ]


def find_all_teachers_for_bind() -> List[Dict]:
    """All active teachers (regardless of bind), ordered by name/id."""
    with db() as conn:
        rows = conn.execute(
            (
                "SELECT id, role, name, email, tef, capacity, tg_id FROM users "
                "WHERE role='teacher' AND is_active=1 ORDER BY LOWER(COALESCE(name,'')) ASC, id ASC"
            )
        ).fetchall()
    return [
        {
            "id": r[0],
            "role": r[1],
            "name": r[2],
            "email": r[3],
            "tef": r[4],
            "capacity": r[5],
            "tg_id": r[6],
        }
        for r in rows
    ]


def is_user_bound(user_id: str) -> bool:
    with db() as conn:
        r = conn.execute(
            "SELECT tg_id FROM users WHERE id=? LIMIT 1", (user_id,)
        ).fetchone()
        return bool(r and r[0])


def get_user_brief(user_id: str) -> Dict | None:
    """Return brief user info by id or None if not found."""
    with db() as conn:
        r = conn.execute(
            (
                "SELECT id, role, name, email, group_name, tg_id "
                "FROM users WHERE id=? LIMIT 1"
            ),
            (user_id,),
        ).fetchone()
    if not r:
        return None
    return {
        "id": r[0],
        "role": r[1],
        "name": r[2],
        "email": r[3],
        "group_name": r[4],
        "tg_id": r[5],
    }


def set_capacity_by_tg(tg_id: str, capacity: int) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE users SET capacity=?, updated_at_utc=strftime('%s','now') WHERE tg_id=?",
            (int(capacity), tg_id),
        )
        conn.commit()
        return cur.rowcount == 1


def set_name_by_tg(tg_id: str, name: str) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE users SET name=?, updated_at_utc=strftime('%s','now') WHERE tg_id=?",
            (name.strip(), tg_id),
        )
        conn.commit()
        return cur.rowcount == 1
