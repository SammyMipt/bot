import asyncio
import importlib
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
        self._answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup: object = None):
        self._answers.append((text, reply_markup))

    @property
    def texts(self) -> list[str]:
        return [t for t, _ in self._answers]


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
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES('300','teacher','Teacher B', ?, ?)",
            (now, now),
        )
        conn.commit()


def test_impersonation_happy_path(monkeypatch):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _seed_users()
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(100, full_name="Owner")
    m = StubMessage(user)

    # Open impersonation
    cb_open = callbacks.build("own", {"action": "impersonation"}, role="owner")
    _run(
        owner.ownui_impersonation(
            StubCallbackQuery(cb_open, user, m), _identity("100", role="owner")
        )
    )
    assert any("Имперсонизация (для техподдержки)" in t for t in m.texts)

    # Start input
    cb_start = callbacks.build("own", {"action": "imp_start"}, role="owner")
    _run(
        owner.ownui_impersonation_start(
            StubCallbackQuery(cb_start, user, m), _identity("100", role="owner")
        )
    )
    assert any("Введите Telegram ID" in t for t in m.texts)

    # Provide valid numeric ID of student (200)
    class TMsg(StubMessage):
        def __init__(self, base, text):
            super().__init__(base.from_user)
            self.text = text

        async def answer(self, text: str, reply_markup: object = None):
            # append to the root message to collect outputs
            m._answers.append((text, reply_markup))

    _run(
        owner.ownui_impersonation_receive(
            TMsg(m, "200"), _identity("100", role="owner")
        )
    )
    assert any("Профиль найден" in t for t in m.texts)

    # Confirm start
    cb_confirm = callbacks.build(
        "own", {"action": "imp_confirm", "tg": "200"}, role="owner"
    )
    _run(
        owner.ownui_impersonation_confirm(
            StubCallbackQuery(cb_confirm, user, m), _identity("100", role="owner")
        )
    )
    assert any("осталось:" in t.lower() for t in m.texts)

    # Stop
    cb_stop = callbacks.build("own", {"action": "imp_stop"}, role="owner")
    _run(
        owner.ownui_impersonation_stop(
            StubCallbackQuery(cb_stop, user, m), _identity("100", role="owner")
        )
    )
    assert any("Имперсонизация завершена" in t for t in m.texts)


def test_impersonation_invalid_and_not_found(monkeypatch):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _seed_users()
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(100, full_name="Owner")
    m = StubMessage(user)

    # Go to input
    cb_open = callbacks.build("own", {"action": "impersonation"}, role="owner")
    _run(
        owner.ownui_impersonation(
            StubCallbackQuery(cb_open, user, m), _identity("100", role="owner")
        )
    )
    cb_start = callbacks.build("own", {"action": "imp_start"}, role="owner")
    _run(
        owner.ownui_impersonation_start(
            StubCallbackQuery(cb_start, user, m), _identity("100", role="owner")
        )
    )

    class TMsg(StubMessage):
        def __init__(self, base, text):
            super().__init__(base.from_user)
            self.text = text

        async def answer(self, text: str, reply_markup: object = None):
            m._answers.append((text, reply_markup))

    # invalid format
    _run(
        owner.ownui_impersonation_receive(
            TMsg(m, "abc"), _identity("100", role="owner")
        )
    )
    assert any("Только цифры" in t for t in m.texts)

    # not found
    _run(
        owner.ownui_impersonation_receive(
            TMsg(m, "999999"), _identity("100", role="owner")
        )
    )
    assert any("не найден" in t for t in m.texts)


def test_impersonation_forbidden_owner_to_owner(monkeypatch):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _seed_users()
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(100, full_name="Owner")
    m = StubMessage(user)
    cb_open = callbacks.build("own", {"action": "impersonation"}, role="owner")
    _run(
        owner.ownui_impersonation(
            StubCallbackQuery(cb_open, user, m), _identity("100", role="owner")
        )
    )
    cb_start = callbacks.build("own", {"action": "imp_start"}, role="owner")
    _run(
        owner.ownui_impersonation_start(
            StubCallbackQuery(cb_start, user, m), _identity("100", role="owner")
        )
    )

    class TMsg(StubMessage):
        def __init__(self, base, text):
            super().__init__(base.from_user)
            self.text = text

        async def answer(self, text: str, reply_markup: object = None):
            m._answers.append((text, reply_markup))

    _run(
        owner.ownui_impersonation_receive(
            TMsg(m, "100"), _identity("100", role="owner")
        )
    )
    assert any("запрещена" in t for t in m.texts)


def test_impersonation_confirm_expired_token(monkeypatch):
    from app.core import callbacks, state_store

    _install_aiogram_stub(monkeypatch)
    _seed_users()
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(100, full_name="Owner")
    m = StubMessage(user)
    cb = callbacks.build("own", {"action": "imp_confirm", "tg": "200"}, role="owner")
    # Simulate expiry: delete key before calling handler
    _, key = callbacks.parse(cb)
    try:
        state_store.delete(key)
    except Exception:
        pass
    scq = StubCallbackQuery(cb, user, m)
    _run(owner.ownui_impersonation_confirm(scq, _identity("100", role="owner")))
    assert any("Сессия истекла" in msg for msg, alert in scq.alerts)
