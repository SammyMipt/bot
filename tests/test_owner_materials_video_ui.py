import asyncio
import importlib
import time

import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _apply_materials_migrations_all():
    import app.db.conn as conn

    for m in [
        "migrations/005_rewire_materials_weeks.sql",
        "migrations/007_materials_versions.sql",
        "migrations/008_materials_hash_scope.sql",
    ]:
        with open(m, "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.db() as c:
            c.executescript(sql)
            c.commit()


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
        self._answers: list[tuple[str, object, str | None]] = []

    async def answer(
        self, text: str, reply_markup: object = None, parse_mode: str | None = None
    ):
        self._answers.append((text, reply_markup, parse_mode))

    @property
    def texts(self) -> list[str]:
        return [t for t, _, _ in self._answers]

    @property
    def markups(self) -> list[object]:
        return [m for _, m, _ in self._answers]


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
    from app.db.conn import db

    # Use any existing owner user id to satisfy FK (uploaded_by)
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE role='owner' ORDER BY created_at_utc LIMIT 1"
        ).fetchone()
        uid = row[0] if row else ""
    return Identity(id=uid, role=role, tg_id=tg_id, name=None)


def _run(awaitable):
    return asyncio.run(awaitable)


def _seed_owner_and_week():
    from app.db.conn import db

    with db() as conn:
        now = int(time.time())
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES('owner-test','owner','Owner', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(1,'Week 1', ?)",
            (now,),
        )
        conn.commit()


def test_owner_video_link_flow(monkeypatch):
    from app.core import callbacks

    _apply_materials_migrations_all()
    _install_aiogram_stub(monkeypatch)
    _seed_owner_and_week()

    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(950, full_name="Owner")
    m = StubMessage(user)

    # Open materials → pick week → pick type v
    cb_root = callbacks.build("own", {"action": "materials"}, role="owner")
    _run(
        owner.ownui_materials(
            StubCallbackQuery(cb_root, user, m), _identity("950", role="owner")
        )
    )

    cb_week = callbacks.build(
        "own", {"action": "materials_week", "week": 1}, role="owner"
    )
    _run(
        owner.ownui_materials_week(
            StubCallbackQuery(cb_week, user, m), _identity("950", role="owner")
        )
    )

    cb_type_v = callbacks.build(
        "own", {"action": "mat_type", "t": "v", "w": 1}, role="owner"
    )
    _run(
        owner.ownui_material_type(
            StubCallbackQuery(cb_type_v, user, m), _identity("950", role="owner")
        )
    )

    # Press upload (expects link)
    cb_upload = callbacks.build(
        "own", {"action": "mat_upload", "w": 1, "t": "v"}, role="owner"
    )
    _run(
        owner.ownui_mat_upload(
            StubCallbackQuery(cb_upload, user, m), _identity("950", role="owner")
        )
    )

    # Send link text
    class _Msg:
        def __init__(self, uid, text):
            self.from_user = StubUser(uid)
            self.text = text
            self._answers = []

        async def answer(self, text: str, reply_markup=None, parse_mode=None):
            m._answers.append((text, reply_markup, parse_mode))

    link = "https://disk.yandex.ru/i/6Cn6Mwzy7648cA"
    _run(owner.ownui_mat_receive_link(_Msg(950, link), _identity("950", role="owner")))
    assert any("✅ Ссылка сохранена" in t for t in m.texts)

    # Render card again and expect clickable link in header
    cb_type_v2 = callbacks.build(
        "own", {"action": "mat_type", "t": "v", "w": 1}, role="owner"
    )
    _run(
        owner.ownui_material_type(
            StubCallbackQuery(cb_type_v2, user, m), _identity("950", role="owner")
        )
    )
    # last added message should contain the URL as anchor
    html_msgs = [t for t, _, pm in m._answers if pm == "HTML"]
    assert any(link in t or "disk.yandex.ru" in t for t in html_msgs)

    # Download (for v) should send link text
    cb_dl = callbacks.build(
        "own", {"action": "mat_download", "w": 1, "t": "v"}, role="owner"
    )
    _run(
        owner.ownui_mat_download(
            StubCallbackQuery(cb_dl, user, m), _identity("950", role="owner")
        )
    )
    assert any("Ссылка на запись лекции" in t for t in m.texts)
