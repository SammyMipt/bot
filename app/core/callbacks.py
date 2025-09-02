from typing import Any, Optional, Tuple

from app.core import state_store
from app.core.errors import StateError

SEPARATOR = ":"


def build(
    op: str,
    params: Any,
    role: Optional[str] = None,
    ttl_sec: int = state_store.DEFAULT_TTL_SEC,
) -> str:
    """Create callback data: f"{op}:{key}" and store params in state_store."""
    assert SEPARATOR not in op, "op must not contain ':'"
    key = state_store.put(action=op, params=params, role=role, ttl_sec=ttl_sec)
    return f"{op}{SEPARATOR}{key}"


def parse(data: str) -> Tuple[str, str]:
    if SEPARATOR not in data:
        return data, ""
    op, key = data.split(SEPARATOR, 1)
    return op, key


def extract(data: str, expected_role: Optional[str] = None) -> Tuple[str, Any]:
    """Parse callback data, fetch params from state_store, return (action, params)."""
    op, key = parse(data)
    if not key:
        raise StateError("no state key in callback data")
    action, params = state_store.get(key, expected_role=expected_role)
    if action != op:
        raise StateError("action mismatch")
    # destroy-on-read to avoid replay
    state_store.delete(key)
    return action, params
