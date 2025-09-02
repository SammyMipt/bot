import json
import time
import uuid
from typing import Any, Optional, Tuple

from app.core.errors import StateExpired, StateNotFound, StateRoleMismatch
from app.db.conn import db

DEFAULT_TTL_SEC = 15 * 60  # 15 minutes


def now() -> int:
    return int(time.time())


def _ensure_table():
    # Table is created by migrations; keep as guard in dev
    with db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS state_store ("
            "key TEXT PRIMARY KEY, role TEXT, action TEXT, params TEXT, "
            "created_at_utc INTEGER NOT NULL, expires_at_utc INTEGER NOT NULL)"
        )


def gen_key() -> str:
    # Short key: first 12 chars of uuid4
    return uuid.uuid4().hex[:12]


def put(
    action: str,
    params: Any,
    role: Optional[str] = None,
    ttl_sec: int = DEFAULT_TTL_SEC,
) -> str:
    """Store action/params with optional role restriction. Returns a generated key."""
    _ensure_table()
    k = gen_key()
    created = now()
    expires = created + max(1, ttl_sec)
    payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
    with db() as conn:
        conn.execute(
            "INSERT INTO state_store(key, role, action, params, created_at_utc, expires_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (k, role, action, payload, created, expires),
        )
        conn.commit()
    return k


def put_at(
    key: str,
    action: str,
    params: Any,
    role: Optional[str] = None,
    ttl_sec: int = DEFAULT_TTL_SEC,
) -> None:
    """Store action/params under a provided key (overwrites if exists)."""
    _ensure_table()
    created = now()
    expires = created + max(1, ttl_sec)
    payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
    with db() as conn:
        conn.execute(
            """
            INSERT INTO state_store(key, role, action, params, created_at_utc, expires_at_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              role=excluded.role,
              action=excluded.action,
              params=excluded.params,
              created_at_utc=excluded.created_at_utc,
              expires_at_utc=excluded.expires_at_utc
            """,
            (key, role, action, payload, created, expires),
        )
        conn.commit()


def get(key: str, expected_role: Optional[str] = None) -> Tuple[str, Any]:
    _ensure_table()
    with db() as conn:
        row = conn.execute(
            "SELECT role, action, params, expires_at_utc FROM state_store WHERE key = ?",
            (key,),
        ).fetchone()
    if row is None:
        raise StateNotFound("state key not found")
    role, action, params, expires = (
        row["role"],
        row["action"],
        row["params"],
        row["expires_at_utc"],
    )
    if expires < now():
        delete(key)
        raise StateExpired("state key expired")
    if expected_role and role and expected_role != role:
        raise StateRoleMismatch(f"expected role {expected_role}, got {role}")
    return action, json.loads(params)


def delete(key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM state_store WHERE key = ?", (key,))
        conn.commit()


def cleanup_expired() -> int:
    """Delete expired records. Returns number of rows removed."""
    with db() as conn:
        cur = conn.execute("DELETE FROM state_store WHERE expires_at_utc < ?", (now(),))
        conn.commit()
        return cur.rowcount
