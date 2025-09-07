import asyncio
import time

import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _install_aiogram_stub(monkeypatch):
    import sys as _sys
    import types as _types

    types_mod = _types.ModuleType("aiogram.types")

    class InlineKeyboardButton:
        def __init__(
            self, text: str, callback_data: str | None = None, url: str | None = None
        ):
            self.text = text
            self.callback_data = callback_data
            self.url = url

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard: list[list[InlineKeyboardButton]]):
            self.inline_keyboard = inline_keyboard

    class User:
        def __init__(self, id: int, full_name: str = ""):
            self.id = id
            self.full_name = full_name

    class Message:
        def __init__(self, from_user: User):
            self.from_user = from_user

    class CallbackQuery:
        def __init__(self, from_user: User, data: str):
            self.from_user = from_user
            self.data = data

    types_mod.InlineKeyboardButton = InlineKeyboardButton
    types_mod.InlineKeyboardMarkup = InlineKeyboardMarkup
    types_mod.User = User
    types_mod.Message = Message
    types_mod.CallbackQuery = CallbackQuery

    aiogram_mod = _types.ModuleType("aiogram")

    class BaseMiddleware:
        async def __call__(self, handler, event, data):
            return await handler(event, data)

    class _F:
        text = object()
        document = object()

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

    aiogram_mod.BaseMiddleware = BaseMiddleware
    aiogram_mod.F = _F
    aiogram_mod.Router = Router
    aiogram_mod.types = types_mod
    filters_mod = _types.ModuleType("aiogram.filters")

    class Command:
        def __init__(self, *_a, **_k):
            pass

    class CommandStart:
        def __init__(self, *_a, **_k):
            pass

    filters_mod.Command = Command
    filters_mod.CommandStart = CommandStart
    _sys.modules["aiogram.filters"] = filters_mod

    _sys.modules["aiogram"] = aiogram_mod
    _sys.modules["aiogram.types"] = types_mod


def _seed_users():
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES('100','owner','Owner', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES('200','student','Student A', ?, ?)",
            (now, now),
        )
        conn.commit()


def _run(awaitable):
    return asyncio.run(awaitable)


async def _capture_handler(_event, data):
    return data


def test_middleware_impersonates_non_owner_ui(monkeypatch):
    _install_aiogram_stub(monkeypatch)
    _seed_users()
    from aiogram.types import User

    from app.bot.middleware.auth_mw import AuthMiddleware
    from app.bot.middleware.auth_mw import CallbackQuery as MWCallbackQuery
    from app.bot.middleware.auth_mw import Message as MWMessage
    from app.core import state_store

    # Activate impersonation for owner(100) -> student(200)
    state_store.put_at(
        f"impersonate:{100}",
        "imp_active",
        {
            "tg_id": "200",
            "role": "student",
            "name": "Student A",
            "exp": state_store.now() + 1800,
        },
        ttl_sec=1800,
    )
    mw = AuthMiddleware()
    owner_msg = MWMessage(User(100, "Owner"))

    data = {}
    out = _run(mw(__import__(__name__).__dict__["_capture_handler"], owner_msg, data))
    assert out["actor"].role == "student"
    assert out.get("principal") is not None
    assert out["principal"].role == "owner"

    # Owner UI should not swap actor
    cq = MWCallbackQuery(User(100, "Owner"), data="own:dummykey")
    data2 = {}
    out2 = _run(mw(__import__(__name__).__dict__["_capture_handler"], cq, data2))
    assert out2["actor"].role == "owner"
    assert out2.get("principal") is None


def test_middleware_guest_when_unknown_user(monkeypatch):
    _install_aiogram_stub(monkeypatch)
    from aiogram.types import User

    from app.bot.middleware.auth_mw import AuthMiddleware
    from app.bot.middleware.auth_mw import Message as MWMessage

    mw = AuthMiddleware()
    msg = MWMessage(User(999, "Ghost"))
    out = _run(mw(__import__(__name__).__dict__["_capture_handler"], msg, {}))
    assert out["actor"].role == "guest"
    assert out["actor"].tg_id == "999"
