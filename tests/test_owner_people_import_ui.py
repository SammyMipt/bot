import asyncio
import csv
import importlib
import io
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

    # Create aiogram.types stub with minimal classes
    types_mod = _types.ModuleType("aiogram.types")

    class InlineKeyboardButton:
        def __init__(self, text: str, callback_data: str):
            self.text = text
            self.callback_data = callback_data

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard: list[list[InlineKeyboardButton]]):
            self.inline_keyboard = inline_keyboard

    class BufferedInputFile:
        def __init__(self, data: bytes, filename: str):
            self.data = data
            self.filename = filename

    class User:
        def __init__(self, id: int, full_name: str = ""):
            self.id = id
            self.full_name = full_name

    types_mod.InlineKeyboardButton = InlineKeyboardButton
    types_mod.InlineKeyboardMarkup = InlineKeyboardMarkup
    types_mod.BufferedInputFile = BufferedInputFile
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
        self._docs: list[tuple[Any, str, str | None]] = []

    async def answer(self, text: str, reply_markup: Any = None):
        self._answers.append((text, reply_markup))

    async def answer_document(self, document: Any, caption: str | None = None):
        # document is aiogram.types.BufferedInputFile (stubbed)
        fname = getattr(document, "filename", None)
        self._docs.append((document, fname, caption))

    @property
    def texts(self) -> list[str]:
        return [t for t, _ in self._answers]

    @property
    def filenames(self) -> list[str]:
        return [name for _, name, _ in self._docs if name]


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


def _csv_bytes(headers: list[str], rows: list[list[str]]) -> io.BytesIO:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return io.BytesIO(buf.getvalue().encode("utf-8"))


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


def test_people_import_teachers_success_extras_and_checksum(monkeypatch):
    from app.core import callbacks
    from app.core.imports_epic5 import TEACHER_HEADERS

    _apply_epic5_migration()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(900, full_name="Owner")
    m = StubMessage(user)

    # Enter teachers import screen
    cb_imp = callbacks.build("own", {"action": "people_imp_teachers"}, role="owner")
    _run(
        owner.ownui_people_imp_teachers(
            StubCallbackQuery(cb_imp, user, m), _identity("900", role="owner")
        )
    )

    # Upload CSV: valid row + row with extra column to be dropped
    content = _csv_bytes(
        TEACHER_HEADERS,
        [
            ["Иванов", "Иван", "Иванович", "ivanov@example.com", "2", "10"],
            ["X", "Y", "", "x@y.com", "1", "5", "EXTRA"],
        ],
    )

    class DocMsg(StubMessage):
        def __init__(self, base: StubMessage, bot_content: io.BytesIO):
            super().__init__(base.from_user)
            self.document = _Doc("doc-teachers")
            self.bot = BotStub(bot_content)

    dmsg = DocMsg(m, content)
    _run(owner.ownui_people_imp_teachers_receive(dmsg, _identity("900", role="owner")))
    # Warning about extra columns
    assert any("Лишние колонки" in t for t in dmsg.texts)
    # Summary and users summary present
    assert any(t.startswith("Импорт завершён:") for t in dmsg.texts)
    assert any("Учителя: всего" in t for t in dmsg.texts)

    # Upload the same file again — must be deduplicated by checksum
    dmsg2 = DocMsg(m, content)
    _run(owner.ownui_people_imp_teachers_receive(dmsg2, _identity("900", role="owner")))
    assert any(
        "Импорт дублируется по checksum" in t for t in dmsg2.texts
    ), f"Got: {dmsg2.texts}"


def test_people_import_students_success_and_summary(monkeypatch):
    from app.core import callbacks
    from app.core.imports_epic5 import STUDENT_HEADERS

    _apply_epic5_migration()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(901, full_name="Owner")
    m = StubMessage(user)

    cb_imp = callbacks.build("own", {"action": "people_imp_students"}, role="owner")
    _run(
        owner.ownui_people_imp_students(
            StubCallbackQuery(cb_imp, user, m), _identity("901", role="owner")
        )
    )

    content = _csv_bytes(
        STUDENT_HEADERS,
        [
            ["Петров", "Пётр", "", "", "IU5-21"],
            ["Сидорова", "Анна", "", "anna@example.com", "IU5-22"],
        ],
    )

    class DocMsg(StubMessage):
        def __init__(self, base: StubMessage, bot_content: io.BytesIO):
            super().__init__(base.from_user)
            self.document = _Doc("doc-students")
            self.bot = BotStub(bot_content)

    dmsg = DocMsg(m, content)
    _run(owner.ownui_people_imp_students_receive(dmsg, _identity("901", role="owner")))
    assert any(t.startswith("Импорт завершён:") for t in dmsg.texts)
    assert any("Студенты: всего" in t for t in dmsg.texts)


def test_people_templates_download(monkeypatch):
    from app.core import callbacks

    _apply_epic5_migration()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    user = StubUser(902, full_name="Owner")
    m = StubMessage(user)

    # Teachers template
    cb_tpl_t = callbacks.build(
        "own", {"action": "people_tpl", "t": "teachers"}, role="owner"
    )
    _run(
        owner.ownui_people_tpl(
            StubCallbackQuery(cb_tpl_t, user, m), _identity("902", role="owner")
        )
    )
    # Students template
    cb_tpl_s = callbacks.build(
        "own", {"action": "people_tpl", "t": "students"}, role="owner"
    )
    _run(
        owner.ownui_people_tpl(
            StubCallbackQuery(cb_tpl_s, user, m), _identity("902", role="owner")
        )
    )

    assert "teachers.csv" in m.filenames
    assert "students.csv" in m.filenames
