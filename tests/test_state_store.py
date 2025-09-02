import time

from app.core import state_store
from app.core.errors import StateExpired, StateNotFound, StateRoleMismatch


def test_put_get_delete_roundtrip():
    key = state_store.put("demo", {"x": 1}, role="owner", ttl_sec=5)
    action, params = state_store.get(key, expected_role="owner")
    assert action == "demo" and params == {"x": 1}
    state_store.delete(key)
    try:
        state_store.get(key)
        assert False, "should not reach"
    except StateNotFound:
        pass


def test_expiry():
    key = state_store.put("demo", {"y": 2}, role=None, ttl_sec=1)
    time.sleep(2.1)
    try:
        state_store.get(key)
        assert False, "expected expired"
    except StateExpired:
        pass


def test_role_mismatch():
    key = state_store.put("demo", {"z": 3}, role="teacher", ttl_sec=5)
    try:
        state_store.get(key, expected_role="student")
        assert False, "expected role mismatch"
    except StateRoleMismatch:
        pass
