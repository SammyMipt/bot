import importlib
import time
from datetime import datetime, timezone
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _apply_weeks_migration():
    import app.db.conn as conn

    with open("migrations/004_course_weeks_schema.sql", "r", encoding="utf-8") as f:
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

    _sys.modules.setdefault("aiogram", aiogram_mod)
    _sys.modules.setdefault("aiogram.types", types_mod)
    _sys.modules.setdefault("aiogram.filters", filters_mod)


class StubUser:
    def __init__(self, uid: int, full_name: str = ""):
        self.id = uid
        self.full_name = full_name


class StubMessage:
    def __init__(self, from_user: StubUser):
        self.from_user = from_user
        self._answers: list[tuple[str, Any]] = []

    async def answer(self, text: str, reply_markup: Any = None):
        self._answers.append((text, reply_markup))

    async def edit_text(
        self, text: str, reply_markup: Any = None, parse_mode: str | None = None
    ):
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


@pytest.mark.asyncio
async def test_course_info_renders_name_weeks_and_deadlines(monkeypatch):
    from app.core import callbacks
    from app.db.conn import db

    _apply_weeks_migration()
    _install_aiogram_stub(monkeypatch)

    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    # Prepare data
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO course(id, name, created_at_utc, updated_at_utc) VALUES(1, 'Physics', ?, ?)",
            (now, now),
        )
        # week 1: future deadline (green)
        fut = int(datetime.now(tz=timezone.utc).timestamp()) + 7 * 86400
        conn.execute(
            "INSERT INTO weeks(week_no, title, created_at_utc, topic, description, deadline_ts_utc) VALUES(1,'W1',?,?,?,?)",
            (now, "Intro", "", fut),
        )
        # week 2: past deadline (red)
        past = int(datetime.now(tz=timezone.utc).timestamp()) - 86400
        conn.execute(
            "INSERT INTO weeks(week_no, title, created_at_utc, topic, description, deadline_ts_utc) VALUES(2,'W2',?,?,?,?)",
            (now, "Kinematics", "", past),
        )
        # week 3: no deadline
        conn.execute(
            "INSERT INTO weeks(week_no, title, created_at_utc, topic, description, deadline_ts_utc) VALUES(3,'W3',?,?,?,NULL)",
            (now, "Vectors", ""),
        )
        conn.commit()

    # Fix current time used in UI for indicators
    monkeypatch.setattr(owner, "_now", lambda: now)

    user = StubUser(700, full_name="Owner")
    m = StubMessage(user)
    cb = callbacks.build("own", {"action": "course_info"}, role="owner")
    await owner.ownui_course_info(
        StubCallbackQuery(cb, user, m), _identity("700", role="owner")
    )

    body = "\n".join(m.texts)
    # Title and name
    assert "<b>Общие сведения о курсе</b>" in body
    assert "<b>Название:</b> Physics" in body
    # Week numbering is plain numbers (no 'W') and deadline formatted as bold date only
    fut_date = datetime.fromtimestamp(fut, timezone.utc).strftime("%Y-%m-%d")
    past_date = datetime.fromtimestamp(past, timezone.utc).strftime("%Y-%m-%d")
    assert f"<b>Неделя 1</b> — Intro — <b>дедлайн {fut_date}</b> 🟢" in body
    assert f"<b>Неделя 2</b> — Kinematics — <b>дедлайн {past_date}</b> 🔴" in body
    # No deadline line
    assert "<b>Неделя 3</b> — Vectors — без дедлайна" in body


@pytest.mark.asyncio
async def test_course_info_pagination(monkeypatch):
    from app.core import callbacks
    from app.db.conn import db

    _apply_weeks_migration()
    _install_aiogram_stub(monkeypatch)

    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO course(id, name, created_at_utc, updated_at_utc) VALUES(1, 'Physics', ?, ?)",
            (now, now),
        )
        for i in range(1, 11):  # 10 weeks → 2 pages (8 + 2)
            conn.execute(
                "INSERT INTO weeks(week_no, title, created_at_utc, topic, description, deadline_ts_utc) VALUES(?, ?, ?, ?, '', NULL)",
                (i, f"W{i}", now, f"Topic {i}"),
            )
        conn.commit()

    user = StubUser(701, full_name="Owner")
    m = StubMessage(user)

    # First page
    cb1 = callbacks.build("own", {"action": "course_info"}, role="owner")
    await owner.ownui_course_info(
        StubCallbackQuery(cb1, user, m), _identity("701", role="owner")
    )
    body1 = "\n".join(m.texts)
    assert "Структура курса (стр. 1/2)" in body1
    assert "<b>Неделя 8</b>" in body1
    assert "<b>Неделя 9</b>" not in body1

    # Second page
    cb2 = callbacks.build(
        "own", {"action": "course_info_page", "page": 1}, role="owner"
    )
    await owner.ownui_course_info_page(
        StubCallbackQuery(cb2, user, m), _identity("701", role="owner")
    )
    body2 = "\n".join(m.texts)
    assert "Структура курса (стр. 2/2)" in body2
    assert "<b>Неделя 9</b>" in body2 or "<b>Неделя 10</b>" in body2
