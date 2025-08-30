import uuid

from app.core import auth
from app.db.conn import db


def test_create_and_get_user():
    tg = f"test_tg_{uuid.uuid4().hex[:8]}"
    try:
        u = auth.create_user(tg, "student", name="Test User")
        g = auth.get_user_by_tg(tg)
        assert g is not None
        assert g.id == u.id
        assert g.role == "student"
    finally:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE tg_id=?", (tg,))
            conn.commit()
