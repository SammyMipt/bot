import asyncio
import importlib
import io
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

    # Create aiogram.types stub with minimal classes
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

    # Create aiogram.filters stub
    filters_mod = _types.ModuleType("aiogram.filters")

    class Command:
        def __init__(self, *_a, **_k):
            pass

    class CommandStart:
        def __init__(self, *_a, **_k):
            pass

    filters_mod.Command = Command
    filters_mod.CommandStart = CommandStart

    # Create aiogram root stub
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


def _csv_bytes(rows):
    out = io.StringIO()
    out.write("week_id,topic,description,deadline\n")
    for r in rows:
        out.write(
            f"{r.get('week_id', '')},{r.get('topic', '')},{r.get('description', '')},{r.get('deadline', '')}\n"
        )
    return io.BytesIO(out.getvalue().encode("utf-8"))


class _Doc:
    def __init__(self, file_id: str):
        self.file_id = file_id


class _File:
    def __init__(self, file_path: str):
        self.file_path = file_path


class BotStub:
    def __init__(self, content: io.BytesIO):
        self._content = content

    async def get_file(self, file_id: str):
        return _File(file_path=f"/{file_id}")

    async def download_file(self, file_path: str):
        # return a fresh buffer positioned at 0
        return io.BytesIO(self._content.getvalue())


def _run(awaitable):
    return asyncio.run(awaitable)


def test_course_init_e2e_success_and_deletes_extras(monkeypatch):
    from app.core import callbacks
    from app.db.conn import db

    _apply_weeks_migration()

    # Prepare an extra week that should be removed by re-init
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(99,'Extra', strftime('%s','now'))"
        )
        conn.commit()

    # Prepare module and identity
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(500, full_name="Owner")
    m = StubMessage(user)

    # Navigate to course screen
    cb_course = callbacks.build("own", {"action": "course"}, role="owner")
    _run(
        owner.ownui_course(
            StubCallbackQuery(cb_course, user, m), _identity("500", role="owner")
        )
    )

    # Start init
    cb_init = callbacks.build("own", {"action": "course_init"}, role="owner")
    _run(
        owner.ownui_course_init(
            StubCallbackQuery(cb_init, user, m), _identity("500", role="owner")
        )
    )

    # Enter course name
    class TextMsg(StubMessage):
        def __init__(self, base: StubMessage, text: str):
            super().__init__(base.from_user)
            self.text = text

    tname = TextMsg(m, "Physics 101")
    _run(owner.ownui_course_init_receive_name(tname, _identity("500", role="owner")))
    assert any("Название курса сохранено" in t for t in tname.texts)

    # Proceed to step 2
    cb_step2 = callbacks.build("own", {"action": "course_init_2"}, role="owner")
    _run(
        owner.ownui_course_init_2(
            StubCallbackQuery(cb_step2, user, m), _identity("500", role="owner")
        )
    )

    # Upload CSV
    csv_content = _csv_bytes(
        [
            {
                "week_id": "W01",
                "topic": "Intro",
                "description": "Desc1",
                "deadline": "2025-01-02",
            },
            {
                "week_id": "2",
                "topic": "Vectors",
                "description": "Desc2",
                "deadline": "2025-01-10 12:00",
            },
        ]
    )

    class DocMsg(StubMessage):
        def __init__(self, base: StubMessage, bot_content: io.BytesIO):
            super().__init__(base.from_user)
            self.document = _Doc("doc1")
            self.bot = BotStub(bot_content)

    dmsg = DocMsg(m, csv_content)
    _run(owner.ownui_course_init_receive_csv(dmsg, _identity("500", role="owner")))
    assert any("Файл принят" in t for t in dmsg.texts)

    # Preview
    cb_step3 = callbacks.build("own", {"action": "course_init_3"}, role="owner")
    _run(
        owner.ownui_course_init_3(
            StubCallbackQuery(cb_step3, user, m), _identity("500", role="owner")
        )
    )
    assert any("Предпросмотр недель" in t for t in m.texts)

    # Allow apply (fresh backup) — patch the symbol used inside ui_owner_stub
    monkeypatch.setattr(owner, "backup_recent", lambda now=None: True)

    cb_done = callbacks.build("own", {"action": "course_init_done"}, role="owner")
    _run(
        owner.ownui_course_init_done(
            StubCallbackQuery(cb_done, user, m), _identity("500", role="owner")
        )
    )
    assert any("Инициализация завершена" in t for t in m.texts)

    # Verify DB
    with db() as conn:
        w = conn.execute("SELECT COUNT(1) FROM weeks WHERE week_no=99").fetchone()[0]
        assert w == 0
        w1 = conn.execute(
            "SELECT topic, description FROM weeks WHERE week_no=1"
        ).fetchone()
        w2 = conn.execute(
            "SELECT topic, description FROM weeks WHERE week_no=2"
        ).fetchone()
        assert (w1[0], w1[1]) == ("Intro", "Desc1") and (w2[0], w2[1]) == (
            "Vectors",
            "Desc2",
        )


def test_course_init_csv_errors_and_backup_block(monkeypatch):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    _apply_weeks_migration()
    importlib.reload(owner)

    user = StubUser(600)
    m = StubMessage(user)

    # Begin init and set name quickly
    cb_init = callbacks.build("own", {"action": "course_init"}, role="owner")
    _run(
        owner.ownui_course_init(
            StubCallbackQuery(cb_init, user, m), _identity("600", role="owner")
        )
    )

    class TextMsg(StubMessage):
        def __init__(self, base: StubMessage, text: str):
            super().__init__(base.from_user)
            self.text = text

    _run(
        owner.ownui_course_init_receive_name(
            TextMsg(m, "Course"), _identity("600", role="owner")
        )
    )

    # Step 2
    cb_step2 = callbacks.build("own", {"action": "course_init_2"}, role="owner")
    _run(
        owner.ownui_course_init_2(
            StubCallbackQuery(cb_step2, user, m), _identity("600", role="owner")
        )
    )

    # Bad headers
    bad_headers = io.BytesIO(b"w,topic,description,deadline\n1,a,b,2025-01-01\n")
    _run(
        owner.ownui_course_init_receive_csv(
            type(
                "DocMsg",
                (),
                {
                    "from_user": user,
                    "document": _Doc("doc1"),
                    "bot": BotStub(bad_headers),
                    "answer": m.answer,
                },
            )(),
            _identity("600", role="owner"),
        )
    )
    assert any("Ошибка формата CSV" in t for t in m.texts)

    # Invalid deadline
    bad_deadline = _csv_bytes(
        [{"week_id": "1", "topic": "T", "description": "", "deadline": "2025/01/02"}]
    )
    _run(
        owner.ownui_course_init_receive_csv(
            type(
                "DocMsg",
                (),
                {
                    "from_user": user,
                    "document": _Doc("doc2"),
                    "bot": BotStub(bad_deadline),
                    "answer": m.answer,
                },
            )(),
            _identity("600", role="owner"),
        )
    )
    assert any("Некорректная дата дедлайна" in t for t in m.texts)

    # Upload valid CSV and preview
    good = _csv_bytes(
        [
            {"week_id": "1", "topic": "A", "description": "", "deadline": ""},
            {"week_id": "2", "topic": "B", "description": "", "deadline": ""},
        ]
    )
    _run(
        owner.ownui_course_init_receive_csv(
            type(
                "DocMsg",
                (),
                {
                    "from_user": user,
                    "document": _Doc("doc3"),
                    "bot": BotStub(good),
                    "answer": m.answer,
                },
            )(),
            _identity("600", role="owner"),
        )
    )
    cb_step3 = callbacks.build("own", {"action": "course_init_3"}, role="owner")
    _run(
        owner.ownui_course_init_3(
            StubCallbackQuery(cb_step3, user, m), _identity("600", role="owner")
        )
    )
    assert any("Предпросмотр недель" in t for t in m.texts)

    # Block by backup policy — patch the symbol used inside ui_owner_stub
    monkeypatch.setattr(owner, "backup_recent", lambda now=None: False)
    cb_done = callbacks.build("own", {"action": "course_init_done"}, role="owner")
    _run(
        owner.ownui_course_init_done(
            StubCallbackQuery(cb_done, user, m), _identity("600", role="owner")
        )
    )
    assert any("E_BACKUP_STALE" in t for t in m.texts)
