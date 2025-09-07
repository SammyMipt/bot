import importlib
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _apply_epic5_and_types_fix():
    import app.db.conn as conn

    with open("migrations/002_epic5_users_assignments.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.db() as c:
        c.executescript(sql)
        c.commit()

    # Apply type alignment migration (TEXT FKs)
    with open("migrations/006_fix_tsa_types.sql", "r", encoding="utf-8") as f:
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

    async def edit_text(self, text: str, reply_markup: Any | None = None):
        self._answers.append((text, reply_markup))

    async def edit_reply_markup(self, reply_markup: Any | None = None):
        self._answers.append(("", reply_markup))

    async def answer_document(self, document: Any, caption: str | None = None):
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


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)


def _setup_users_and_weeks(students: int, teachers: list[tuple[int, int]]):
    # teachers: list of (tg_id, capacity)
    import time

    from app.core.auth import create_user
    from app.db.conn import db

    # weeks
    with db() as c:
        now = int(time.time())
        for w in (1, 2):
            c.execute(
                "INSERT INTO weeks(week_no, title, created_at_utc) VALUES(?,?,?)",
                (w, f"W{w}", now),
            )
        c.commit()

    # students
    for i in range(students):
        create_user(tg_id=str(1000 + i), role="student", name=f"S{i + 1}")

    # teachers
    from app.db.conn import db as _db

    for i, cap in teachers:
        u = create_user(tg_id=str(i), role="teacher", name=f"T{i}")
        with _db() as c:
            c.execute("UPDATE users SET capacity=? WHERE id=?", (cap, u.id))
            c.commit()


def test_assignment_preview_commit_success(monkeypatch):
    from app.core import callbacks

    _apply_epic5_and_types_fix()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    # 3 students, 2 teachers with capacity 2 each (sum 4 >= 3)
    _setup_users_and_weeks(students=3, teachers=[(200, 2), (201, 2)])

    user = StubUser(900, full_name="Owner")
    m = StubMessage(user)

    # Preview: canonical callback a=as; s=p
    cb_p = callbacks.build("own", {"a": "as", "s": "p"}, role="owner")
    _run(
        owner.ownui_people_matrix_preview(
            StubCallbackQuery(cb_p, user, m), _identity("900", role="owner")
        )
    )

    # Commit: a=as; s=c
    cb_c = callbacks.build("own", {"a": "as", "s": "c"}, role="owner")
    _run(
        owner.ownui_people_matrix_commit(
            StubCallbackQuery(cb_c, user, m), _identity("900", role="owner")
        )
    )

    # Verify DB rows = weeks * students = 2 * 3 = 6
    from app.db.conn import db

    with db() as c:
        cnt = c.execute("SELECT COUNT(*) FROM teacher_student_assignments").fetchone()[
            0
        ]
    assert cnt == 6


def test_assignment_preview_insufficient_capacity(monkeypatch):
    from app.core import callbacks

    _apply_epic5_and_types_fix()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    # 3 students, 1 teacher cap 2 (sum 2 < 3)
    _setup_users_and_weeks(students=3, teachers=[(300, 2)])

    user = StubUser(901, full_name="Owner")
    m = StubMessage(user)
    cb_p = callbacks.build("own", {"a": "as", "s": "p"}, role="owner")
    cq = StubCallbackQuery(cb_p, user, m)
    _run(owner.ownui_people_matrix_preview(cq, _identity("901", role="owner")))
    assert any(
        "Недостаточная суммарная вместимость" in txt and ok for txt, ok in cq.alerts
    )


def test_assignment_commit_revalidation_capacity_drop(monkeypatch):
    from app.core import callbacks
    from app.db.conn import db

    _apply_epic5_and_types_fix()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    # 3 students, 2 teachers capacity 2+1 = 3 (ok)
    _setup_users_and_weeks(students=3, teachers=[(400, 2), (401, 1)])

    user = StubUser(902, full_name="Owner")
    m = StubMessage(user)

    cb_p = callbacks.build("own", {"a": "as", "s": "p"}, role="owner")
    _run(
        owner.ownui_people_matrix_preview(
            StubCallbackQuery(cb_p, user, m), _identity("902", role="owner")
        )
    )

    # Drop capacity to trigger revalidation failure
    with db() as c:
        c.execute("UPDATE users SET capacity=0 WHERE role='teacher'")
        c.commit()

    cb_c = callbacks.build("own", {"a": "as", "s": "c"}, role="owner")
    cq = StubCallbackQuery(cb_c, user, m)
    _run(owner.ownui_people_matrix_commit(cq, _identity("902", role="owner")))
    assert any(
        "Недостаточная суммарная вместимость" in txt and ok for txt, ok in cq.alerts
    )


def test_assignment_export_no_matrix(monkeypatch):
    from app.core import callbacks

    _apply_epic5_and_types_fix()
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    # Ensure module loaded, then monkeypatch backup_recent to allow export path
    importlib.reload(owner)
    monkeypatch.setattr(owner, "backup_recent", lambda: True)

    user = StubUser(903, full_name="Owner")
    m = StubMessage(user)
    cb = callbacks.build("own", {"action": "rep_matrix"}, role="owner")
    cq = StubCallbackQuery(cb, user, m)
    _run(owner.ownui_reports_matrix(cq, _identity("903", role="owner")))
    assert any("Матрица назначений не создана" in txt and ok for txt, ok in cq.alerts)
