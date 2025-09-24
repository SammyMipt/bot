import importlib

import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _install_aiogram_stub(monkeypatch):
    import sys
    import types

    types_mod = types.ModuleType("aiogram.types")

    class InlineKeyboardButton:
        def __init__(self, text: str, callback_data: str | None = None):
            self.text = text
            self.callback_data = callback_data

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    class User:
        def __init__(self, id: int):
            self.id = id

    class BufferedInputFile:
        def __init__(self, data: bytes, filename: str):
            self.data = data
            self.filename = filename

    types_mod.InlineKeyboardButton = InlineKeyboardButton
    types_mod.InlineKeyboardMarkup = InlineKeyboardMarkup
    types_mod.User = User
    types_mod.BufferedInputFile = BufferedInputFile

    filters_mod = types.ModuleType("aiogram.filters")

    class Command:
        def __init__(self, *_a, **_k):
            pass

    filters_mod.Command = Command

    aiogram_mod = types.ModuleType("aiogram")

    class Router:
        def __init__(self, name: str | None = None):
            self.name = name

        def message(self, *_a, **_k):
            def deco(func):
                return func

            return deco

        def callback_query(self, *_a, **_k):
            def deco(func):
                return func

            return deco

    class _F:
        text = object()
        document = object()

    aiogram_mod.Router = Router
    aiogram_mod.F = _F
    aiogram_mod.types = types_mod
    aiogram_mod.filters = filters_mod

    sys.modules["aiogram"] = aiogram_mod
    sys.modules["aiogram.types"] = types_mod
    sys.modules["aiogram.filters"] = filters_mod


class StubUser:
    def __init__(self, uid: int):
        self.id = uid


class StubMessage:
    def __init__(self, text: str, from_user: StubUser):
        self.text = text
        self.from_user = from_user
        self._answers: list[str] = []

    async def answer(self, text: str, **_kwargs):
        self._answers.append(text)

    @property
    def answers(self) -> list[str]:
        return self._answers


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)


def _apply_users_migration():
    import app.db.conn as conn

    with open("migrations/002_epic5_users_assignments.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.db() as c:
        c.executescript(sql)
        c.commit()


def test_owner_set_email_updates_profile(monkeypatch):
    _install_aiogram_stub(monkeypatch)
    _apply_users_migration()

    from app.core.auth import create_user
    from app.db.conn import db

    owner_identity = create_user("tg-owner", "owner", name="Owner")

    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(1001)
    msg = StubMessage("/set_email owner@example.com", user)

    _run(owner.owner_set_email_cmd(msg, owner_identity))

    assert any("Email обновлён" in text for text in msg.answers)
    with db() as conn:
        row = conn.execute(
            "SELECT email FROM users WHERE id=?",
            (owner_identity.id,),
        ).fetchone()
    assert row and row[0] == "owner@example.com"


def test_owner_set_email_validates_format(monkeypatch):
    _install_aiogram_stub(monkeypatch)
    _apply_users_migration()

    from app.core.auth import create_user
    from app.db.conn import db

    owner_identity = create_user("tg-owner-2", "owner", name="Owner")

    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(1002)
    msg = StubMessage("/set_email not-an-email", user)

    _run(owner.owner_set_email_cmd(msg, owner_identity))

    assert any("Некорректный email" in text for text in msg.answers)
    with db() as conn:
        row = conn.execute(
            "SELECT email FROM users WHERE id=?",
            (owner_identity.id,),
        ).fetchone()
    assert row and (row[0] is None)
