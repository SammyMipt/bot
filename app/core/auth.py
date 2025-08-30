from dataclasses import dataclass
from typing import Optional

from aiogram.types import User as TgUser

from app.core.roles import ALL_ROLES, STUDENT
from app.db.conn import db


@dataclass
class Identity:
    id: int
    role: str
    tg_id: str
    name: Optional[str]


def _row_to_identity(row) -> Identity:
    return Identity(
        id=row["id"], role=row["role"], tg_id=row["tg_id"], name=row["name"]
    )


def get_user_by_tg(tg_id: str) -> Optional[Identity]:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    return _row_to_identity(row) if row else None


def create_user(tg_id: str, role: str, name: Optional[str] = None) -> Identity:
    assert role in ALL_ROLES, "invalid role"
    import time

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES(?,?,?,?,?)",
            (tg_id, role, name, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    return _row_to_identity(row)


def ensure_user(tg: TgUser) -> Identity:
    tg_id = str(tg.id)
    user = get_user_by_tg(tg_id)
    if user:
        return user
    return create_user(tg_id, STUDENT, name=(tg.full_name or None))
