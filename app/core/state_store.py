import json
import time
import uuid
from typing import Any, Optional

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
            "key TEXT PRIMARY KEY, role TEXT, value_json TEXT NOT NULL, "
            "created_at_utc INTEGER NOT NULL, expires_at_utc INTEGER NOT NULL)"
        )


def gen_key() -> str:
    # Short key: first 12 chars of uuid4
    return uuid.uuid4().hex[:12]


def put(value: Any, role: Optional[str] = None, ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    """Store value with optional role restriction. Returns a generated key."""
    _ensure_table()
    k = gen_key()
    created = now()
    expires = created + max(1, ttl_sec)
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with db() as conn:
        conn.execute(
            "INSERT INTO state_store(key, role, value_json, created_at_utc, expires_at_utc) "
            "VALUES (?, ?, ?, ?, ?)",
            (k, role, payload, created, expires),
        )
        conn.commit()
    return k


def put_at(
    key: str, value: Any, role: Optional[str] = None, ttl_sec: int = DEFAULT_TTL_SEC
) -> None:
    """Store value under a provided key (overwrites if exists)."""
    _ensure_table()
    created = now()
    expires = created + max(1, ttl_sec)
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with db() as conn:
        conn.execute(
            """
            INSERT INTO state_store(key, role, value_json, created_at_utc, expires_at_utc)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              role=excluded.role,
              value_json=excluded.value_json,
              created_at_utc=excluded.created_at_utc,
              expires_at_utc=excluded.expires_at_utc
            """,
            (key, role, payload, created, expires),
        )
        conn.commit()


def get(key: str, expected_role: Optional[str] = None) -> Any:
    _ensure_table()
    with db() as conn:
        row = conn.execute(
            "SELECT role, value_json, expires_at_utc FROM state_store WHERE key = ?",
            (key,),
        ).fetchone()
    if row is None:
        raise StateNotFound("state key not found")
    role, value_json, expires = row["role"], row["value_json"], row["expires_at_utc"]
    if expires < now():
        delete(key)
        raise StateExpired("state key expired")
    if expected_role and role and expected_role != role:
        raise StateRoleMismatch(f"expected role {expected_role}, got {role}")
    return json.loads(value_json)


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
