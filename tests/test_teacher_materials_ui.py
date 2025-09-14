import asyncio
import importlib
import time


def _apply_materials_migrations_all():
    import app.db.conn as conn

    with conn.db() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(weeks)").fetchall()}
        need_004 = "topic" not in cols

    migrations = []
    if need_004:
        migrations.append("migrations/004_course_weeks_schema.sql")
    migrations += [
        "migrations/005_rewire_materials_weeks.sql",
        "migrations/007_materials_versions.sql",
        "migrations/008_materials_hash_scope.sql",
    ]
    for m in migrations:
        with open(m, "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.db() as c:
            c.executescript(sql)
            c.commit()


def _install_aiogram_stub(monkeypatch):
    import sys
    import types

    types_mod = types.ModuleType("aiogram.types")

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

    sys.modules["aiogram"] = aiogram_mod
    sys.modules["aiogram.types"] = types_mod
    sys.modules["aiogram.filters"] = filters_mod


class StubUser:
    def __init__(self, uid: int, full_name: str = ""):
        self.id = uid
        self.full_name = full_name


class StubMessage:
    def __init__(self, from_user: StubUser):
        self.from_user = from_user
        self._answers: list[tuple[str, object, str | None]] = []
        self._docs: list[tuple[object, str | None, str | None]] = []

    async def answer(
        self,
        text: str,
        reply_markup: object = None,
        parse_mode: str | None = None,
        **_k,
    ):
        self._answers.append((text, reply_markup, parse_mode))

    async def answer_document(
        self,
        document: object,
        caption: str | None = None,
        parse_mode: str | None = None,
        **_k,
    ):
        self._docs.append((document, caption, parse_mode))

    @property
    def markups(self) -> list[object]:
        return [m for _, m, _ in self._answers]

    @property
    def documents(self) -> list[tuple[object, str | None, str | None]]:
        return self._docs


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


def _identity(tg_id: str, role: str = "teacher"):
    from app.core.auth import Identity
    from app.db.conn import db

    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        uid = row[0] if row else ""
    return Identity(id=uid, role=role, tg_id=tg_id, name=None)


def _run(awaitable):
    return asyncio.run(awaitable)


def _seed_teacher_and_week():
    from app.core.files import save_blob
    from app.core.repos_epic4 import insert_week_material_file
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES('123','teacher','Teacher',?,?)",
            (now, now),
        )
        row = conn.execute("SELECT id FROM users WHERE tg_id='123'").fetchone()
        tid = row[0]
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(1,'Week 1',?)",
            (now,),
        )
        conn.commit()
    blob = save_blob(b"data", prefix="materials", suggested_name="demo.txt")
    insert_week_material_file(
        1,
        tid,
        blob.path,
        blob.sha256,
        blob.size_bytes,
        "text/plain",
        visibility="public",
        type="p",
        original_name="demo.txt",
    )


def test_teacher_materials_flow(monkeypatch):
    from app.core import callbacks

    _apply_materials_migrations_all()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher_and_week()

    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123, full_name="Teacher")
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    cb_root = callbacks.build("t", {"action": "materials"}, role="teacher")
    _run(teacher.tui_materials(StubCallbackQuery(cb_root, user, m), ident))
    markup = m.markups[-1]
    btn_texts = [b.text for row in markup.inline_keyboard for b in row]
    assert any("Неделя 1" in t for t in btn_texts)

    cb_week = callbacks.build(
        "t", {"action": "materials_week", "week": 1}, role="teacher"
    )
    _run(teacher.tui_materials_week(StubCallbackQuery(cb_week, user, m), ident))

    cb_send = callbacks.build(
        "t", {"action": "materials_send", "week": 1, "t": "p"}, role="teacher"
    )
    cq_send = StubCallbackQuery(cb_send, user, m)
    _run(teacher.tui_materials_send(cq_send, ident))
    assert m.documents
    assert any("Файл отправлен" in t for t, _ in cq_send.alerts)
