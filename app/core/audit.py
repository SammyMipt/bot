import json
import time
from typing import Optional

from app.db.conn import db


def log(
    event: str,
    actor_id: Optional[int],
    *,
    as_user_id: Optional[int] = None,
    as_role: Optional[str] = None,
    object_type: Optional[str] = None,
    object_id: Optional[int] = None,
    meta: Optional[dict] = None,
    request_id: Optional[str] = None
) -> None:
    ts = int(time.time())
    with db() as conn:
        conn.execute(
            (
                "INSERT INTO audit_log("
                "ts_utc, request_id, actor_id, as_user_id, as_role, "
                "event, object_type, object_id, meta_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                ts,
                request_id,
                actor_id,
                as_user_id,
                as_role,
                event,
                object_type,
                object_id,
                json.dumps(meta or {}),
            ),
        )
        conn.commit()
