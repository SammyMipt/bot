import asyncio
import importlib
import time


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

    types_mod.InlineKeyboardButton = InlineKeyboardButton
    types_mod.InlineKeyboardMarkup = InlineKeyboardMarkup
    types_mod.User = User

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

    async def answer(
        self,
        text: str,
        reply_markup: object = None,
        parse_mode: str | None = None,
        **_k,
    ):
        self._answers.append((text, reply_markup, parse_mode))

    async def edit_text(
        self, text: str, reply_markup: object = None, parse_mode: str | None = None
    ):
        self._answers.append((text, reply_markup, parse_mode))

    @property
    def answers(self) -> list[tuple[str, object, str | None]]:
        return self._answers

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


def _identity(tg_id: str, role: str = "student"):
    from app.core.auth import Identity
    from app.db.conn import db

    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        uid = row[0] if row else ""
    return Identity(id=uid, role=role, tg_id=tg_id, name=None)


def _run(awaitable):
    return asyncio.run(awaitable)


def _seed_student_and_week():
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES('s-1','student','Student One',?,?)",
            (now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(3,'Week 3',?)",
            (now,),
        )
        conn.commit()


def _apply_weeks_migration():
    import app.db.conn as conn

    # Run only if weeks.topic is missing
    with conn.db() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(weeks)").fetchall()}
        need_004 = "topic" not in cols
    if need_004:
        with open("migrations/004_course_weeks_schema.sql", "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.db() as c:
            c.executescript(sql)
            c.commit()


def test_student_main_menu_and_weeks(monkeypatch):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _apply_weeks_migration()
    _seed_student_and_week()

    from app.bot import ui_student_stub as student

    importlib.reload(student)

    user = StubUser(1001, full_name="Student")
    m = StubMessage(user)
    ident = _identity("s-1", role="student")

    # Open main menu via command handler
    _run(student.student_menu_cmd(m, ident))
    assert any("Главное меню студента" in t for t, _, _ in m.answers)

    # Open weeks list
    cb_weeks = callbacks.build("s", {"action": "weeks"}, role="student")
    _run(student.sui_weeks(StubCallbackQuery(cb_weeks, user, m), ident))
    markup = m.markups[-1]
    btn_texts = [b.text for row in markup.inline_keyboard for b in row]
    assert any("W03" in t or "Week 3" in t for t in btn_texts)


def test_student_week_menu_and_actions_stubs(monkeypatch):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _apply_weeks_migration()
    _seed_student_and_week()

    from app.bot import ui_student_stub as student

    importlib.reload(student)

    user = StubUser(1001, full_name="Student")
    m = StubMessage(user)
    ident = _identity("s-1", role="student")

    # Open week menu
    cb_week = callbacks.build("s", {"action": "week_menu", "week": 3}, role="student")
    _run(student.sui_week_menu(StubCallbackQuery(cb_week, user, m), ident))
    assert any("W03" in t for t, _, _ in m.answers)

    # Trigger a stub action
    cb_info = callbacks.build("s", {"action": "week_info", "week": 3}, role="student")
    cq = StubCallbackQuery(cb_info, user, m)
    _run(student.sui_week_action_stub(cq, ident))
    assert any("Функция в разработке" in t for t, _, _ in m.answers)
    # nav keyboard present
    markup = m.markups[-1]
    last_row = markup.inline_keyboard[-1]
    assert any("Главное меню" in b.text for b in last_row)
    assert any("Назад" in b.text for b in last_row)


def test_student_top_level_stubs_and_expired_state(monkeypatch):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _apply_weeks_migration()
    _seed_student_and_week()

    from app.bot import ui_student_stub as student

    importlib.reload(student)

    user = StubUser(1001, full_name="Student")
    m = StubMessage(user)
    ident = _identity("s-1", role="student")

    # Top-level stub: my_bookings
    cb_book = callbacks.build("s", {"action": "my_bookings"}, role="student")
    cq1 = StubCallbackQuery(cb_book, user, m)
    _run(student.sui_top_level_stub(cq1, ident))
    assert any("📅 Мои записи" in t for t, _, _ in m.answers)

    # Reuse the same callback to emulate expired/destroyed state
    cq2 = StubCallbackQuery(cb_book, user, m)
    _run(student.sui_top_level_stub(cq2, ident))
    # Expect an expired toast
    assert any("Сессия истекла" in t for t, alert in cq2.alerts if alert)


def test_access_denied_for_non_student(monkeypatch):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _apply_weeks_migration()
    _seed_student_and_week()

    from app.bot import ui_student_stub as student

    importlib.reload(student)

    user = StubUser(1001, full_name="TeacherTrying")
    m = StubMessage(user)
    ident = _identity("s-1", role="teacher")

    cb_weeks = callbacks.build("s", {"action": "weeks"}, role="student")
    cq = StubCallbackQuery(cb_weeks, user, m)
    _run(student.sui_weeks(cq, ident))
    # Access denied alert
    assert any("Доступ запрещён" in t for t, alert in cq.alerts if alert)
