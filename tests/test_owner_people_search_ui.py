import asyncio
import importlib
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _apply_epic5_migration():
    import app.db.conn as conn

    with open("migrations/002_epic5_users_assignments.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.db() as c:
        c.executescript(sql)
        c.commit()


def _install_aiogram_stub(monkeypatch):
    import sys as _sys
    import types as _types

    types_mod = _types.ModuleType("aiogram.types")

    class InlineKeyboardButton:
        def __init__(self, text: str, callback_data: str):
            self.text = text
            self.callback_data = callback_data

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard: list[list[InlineKeyboardButton]]):
            self.inline_keyboard = inline_keyboard

    class User:
        def __init__(self, id: int, full_name: str = ""):
            self.id = id
            self.full_name = full_name

    types_mod.InlineKeyboardButton = InlineKeyboardButton
    types_mod.InlineKeyboardMarkup = InlineKeyboardMarkup
    types_mod.User = User

    filters_mod = _types.ModuleType("aiogram.filters")

    class Command:
        def __init__(self, *_a, **_k):
            pass

    class CommandStart:
        def __init__(self, *_a, **_k):
            pass

    filters_mod.Command = Command
    filters_mod.CommandStart = CommandStart

    aiogram_mod = _types.ModuleType("aiogram")

    class Router:
        def __init__(self, name: str | None = None):
            self.name = name

        def message(self, *_a, **_k):
            def deco(f):
                return f

            return deco

        def callback_query(self, *_a, **_k):
            def deco(f):
                return f

            return deco

    class _F:
        text = object()
        document = object()

    aiogram_mod.Router = Router
    aiogram_mod.F = _F
    aiogram_mod.types = types_mod
    aiogram_mod.filters = filters_mod

    _sys.modules["aiogram"] = aiogram_mod
    _sys.modules["aiogram.types"] = types_mod
    _sys.modules["aiogram.filters"] = filters_mod


class StubUser:
    def __init__(self, uid: int, full_name: str = ""):
        self.id = uid
        self.full_name = full_name


class StubMessage:
    def __init__(self, from_user: StubUser):
        self.from_user = from_user
        self._answers: list[tuple[str, Any]] = []

    async def answer(
        self, text: str, reply_markup: Any = None, parse_mode: str | None = None
    ):
        self._answers.append((text, reply_markup, parse_mode))

    @property
    def texts(self) -> list[str]:
        return [t for t, _, _ in self._answers]


class StubCallbackQuery:
    def __init__(self, data: str, from_user: StubUser, message: StubMessage):
        self.data = data
        self.from_user = from_user
        self.message = message
        self._alerts: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False):
        self._alerts.append((text, show_alert))

    @property
    def alerts(self) -> list[tuple[str, bool]]:
        return self._alerts


def _identity(tg_id: str, role: str = "guest"):
    from app.core.auth import Identity

    return Identity(id="", role=role, tg_id=tg_id, name=None)


def _run(awaitable):
    return asyncio.run(awaitable)


def _insert_user(
    role: str,
    name: str,
    email: str | None = None,
    group_name: str | None = None,
    tg_id: str | None = None,
    capacity: int | None = None,
):
    import app.db.conn as conn

    with conn.db() as c:
        c.execute(
            (
                "INSERT INTO users(tg_id, role, name, email, group_name, capacity, created_at_utc, updated_at_utc) "
                "VALUES(?,?,?,?,?,?, strftime('%s','now'), strftime('%s','now'))"
            ),
            (tg_id, role, name, email, group_name, capacity),
        )
        c.commit()


def test_search_teachers_list_and_profile(monkeypatch):
    from app.core import callbacks

    _apply_epic5_migration()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    # Seed teachers
    for i in range(1, 13):
        _insert_user(
            "teacher", f"Teacher {i:02d}", email=f"t{i}@ex.com", capacity=5 + i
        )

    user = StubUser(910, full_name="Owner")
    m = StubMessage(user)

    # Open search root
    cb_root = callbacks.build("own", {"action": "people_search"}, role="owner")
    _run(
        owner.ownui_people_search_start(
            StubCallbackQuery(cb_root, user, m), _identity("910", role="owner")
        )
    )

    # Open teachers list page 0
    cb_t = callbacks.build("own", {"action": "ps_t_list", "p": 0}, role="owner")
    _run(
        owner.ownui_ps_t_list(
            StubCallbackQuery(cb_t, user, m), _identity("910", role="owner")
        )
    )
    assert any("Преподаватели:" in t for t in m.texts)

    # Open specific teacher profile (take first from DB)
    from app.db.conn import db

    with db() as conn:
        tid = conn.execute(
            "SELECT id FROM users WHERE role='teacher' ORDER BY name LIMIT 1"
        ).fetchone()[0]
    cb_prof = callbacks.build(
        "own", {"action": "people_profile", "uid": tid}, role="owner"
    )
    _run(
        owner.ownui_people_profile(
            StubCallbackQuery(cb_prof, user, m), _identity("910", role="owner")
        )
    )
    assert any("Роль:" in t for t in m.texts)
    assert any("Максимум студентов" in t for t in m.texts)


def test_search_students_groups_and_names(monkeypatch):
    from app.core import callbacks

    _apply_epic5_migration()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    # Seed students in two groups
    for i in range(1, 8):
        _insert_user("student", f"Student G1-{i}", group_name="G1")
    for i in range(1, 5):
        _insert_user("student", f"Student G2-{i}", group_name="G2")

    user = StubUser(911, full_name="Owner")
    m = StubMessage(user)

    # Open search root → groups
    cb_root = callbacks.build("own", {"action": "people_search"}, role="owner")
    _run(
        owner.ownui_people_search_start(
            StubCallbackQuery(cb_root, user, m), _identity("911", role="owner")
        )
    )
    cb_g = callbacks.build("own", {"action": "ps_s_groups", "p": 0}, role="owner")
    _run(
        owner.ownui_ps_s_groups(
            StubCallbackQuery(cb_g, user, m), _identity("911", role="owner")
        )
    )
    assert any("Группы студентов:" in t for t in m.texts)

    # Open names for group G1
    cb_n = callbacks.build(
        "own", {"action": "ps_s_names", "g": "G1", "p": 0}, role="owner"
    )
    _run(
        owner.ownui_ps_s_names(
            StubCallbackQuery(cb_n, user, m), _identity("911", role="owner")
        )
    )
    assert any("Студенты группы G1:" in t for t in m.texts)

    # Open a student's profile
    from app.db.conn import db

    with db() as conn:
        sid = conn.execute(
            "SELECT id FROM users WHERE role='student' AND group_name='G1' ORDER BY name LIMIT 1"
        ).fetchone()[0]
    cb_prof = callbacks.build(
        "own", {"action": "people_profile", "uid": sid}, role="owner"
    )
    _run(
        owner.ownui_people_profile(
            StubCallbackQuery(cb_prof, user, m), _identity("911", role="owner")
        )
    )
    assert any("Группа:" in t for t in m.texts)
