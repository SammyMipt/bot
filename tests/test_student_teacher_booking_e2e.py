import asyncio
import importlib
import time


def _apply_migrations():
    import app.db.conn as conn

    for path in (
        "migrations/002_epic5_users_assignments.sql",
        "migrations/004_course_weeks_schema.sql",
        "migrations/009_slots_location.sql",
        "migrations/011_users_tz.sql",
        "migrations/013_slot_enrollments_week.sql",
    ):
        try:
            with open(path, "r", encoding="utf-8") as f:
                sql = f.read()
            with conn.db() as c:
                try:
                    c.executescript(sql)
                    c.commit()
                except Exception:
                    pass
        except FileNotFoundError:
            pass


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


class _BotStub:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []  # (tg_id, text)

    async def send_message(self, chat_id: str, text: str, **_k):
        self.sent.append((str(chat_id), text))


class StubMessage:
    def __init__(self, from_user: StubUser, bot: _BotStub | None = None):
        self.from_user = from_user
        self._answers: list[tuple[str, object, str | None]] = []
        self.bot = bot

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

    async def edit_reply_markup(self, reply_markup: object | None = None):
        # store as empty text with markup change
        self._answers.append(("", reply_markup, None))

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


def _identity(tg_id: str, role: str):
    from app.core.auth import Identity
    from app.db.conn import db

    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        uid = row[0] if row else ""
    return Identity(id=uid, role=role, tg_id=tg_id, name=None)


def _run(awaitable):
    return asyncio.run(awaitable)


def _mk_user(tg_id: str, role: str, name: str | None = None) -> str:
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES(?,?,?,?,?)",
            (tg_id, role, name or tg_id, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return row[0]


def _mk_week(week_no: int, *, deadline_ts_utc: int | None = None):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(?,?,?)",
            (week_no, f"W{week_no}", now),
        )
        if deadline_ts_utc is not None:
            conn.execute(
                "UPDATE weeks SET deadline_ts_utc=? WHERE week_no=?",
                (deadline_ts_utc, week_no),
            )
        conn.commit()


def _assign_teacher(week_no: int, teacher_id: str, student_id: str):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO teacher_student_assignments(week_no, teacher_id, student_id, created_at_utc) VALUES(?,?,?,?)",
            (week_no, teacher_id, student_id, now),
        )
        conn.commit()


def _mk_slot(
    created_by: str, starts_at_utc: int, duration: int, cap: int, status: str = "open"
) -> int:
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO slots(starts_at_utc, duration_min, capacity, status, created_by, created_at_utc) VALUES(?,?,?,?,?,?)",
            (starts_at_utc, duration, cap, status, created_by, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def test_e2e_student_books_and_teacher_deletes_sends_notifications(
    monkeypatch, db_tmpdir
):
    from app.core import callbacks
    from app.db.conn import db

    _apply_migrations()
    _install_aiogram_stub(monkeypatch)

    # Import after stubs
    from app.bot import ui_student_stub as student
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(student)
    importlib.reload(teacher)

    # Seed users and data
    t_id = _mk_user("t-e2e", "teacher", "Teacher E2E")
    s_id = _mk_user("s-e2e", "student", "Student E2E")
    future = int(time.time()) + 3600
    week_no = 12
    _mk_week(week_no, deadline_ts_utc=future + 7200)
    _assign_teacher(week_no, t_id, s_id)
    sid = _mk_slot(t_id, future, 30, 2, status="open")

    s_user = StubUser(1001, full_name="Student")
    s_msg = StubMessage(s_user)
    s_ident = _identity("s-e2e", role="student")
    # Sanity: repo sees available slots for this student/week
    from app.core.bookings_repo import list_available_slots_for_week

    assert any(s.id == sid for s in list_available_slots_for_week(s_ident.id, week_no))

    # Student opens booking for the week
    cb_wb = callbacks.build(
        "s", {"action": "week_book", "week": week_no}, role="student"
    )
    _run(student.sui_week_book(StubCallbackQuery(cb_wb, s_user, s_msg), s_ident))
    # Should have at least one slot row with a callback
    kb = s_msg.markups[-1]
    pick_btn = next(
        b
        for row in kb.inline_keyboard
        for b in row
        if getattr(b, "callback_data", None)
    )
    _run(
        student.sui_book_slot_pick(
            StubCallbackQuery(pick_btn.callback_data, s_user, s_msg), s_ident
        )
    )
    # Confirm button now present
    kb2 = s_msg.markups[-1]
    conf_btn = next(
        b
        for row in kb2.inline_keyboard
        for b in row
        if getattr(b, "callback_data", None)
    )
    _run(
        student.sui_book_slot_do(
            StubCallbackQuery(conf_btn.callback_data, s_user, s_msg), s_ident
        )
    )

    # DB: enrollment exists and is booked
    with db() as conn:
        row = conn.execute(
            "SELECT status FROM slot_enrollments WHERE slot_id=? AND user_id=?",
            (sid, s_ident.id),
        ).fetchone()
        assert row and row[0] == "booked"

    # Teacher deletes slot → auto-cancel + notifications
    t_user = StubUser(2001, full_name="Teacher")
    bot = _BotStub()
    t_msg = StubMessage(t_user, bot=bot)
    t_ident = _identity("t-e2e", role="teacher")
    cb_del = callbacks.build("t", {"action": "sch_slot_del", "id": sid}, role="teacher")
    _run(teacher.tui_sch_slot_del(StubCallbackQuery(cb_del, t_user, t_msg), t_ident))

    with db() as conn:
        st = conn.execute("SELECT status FROM slots WHERE id=?", (sid,)).fetchone()[0]
        enr = conn.execute(
            "SELECT status FROM slot_enrollments WHERE slot_id=? AND user_id=?",
            (sid, s_ident.id),
        ).fetchone()[0]
        # Audit entries exist
        cnt_auto = conn.execute(
            "SELECT COUNT(1) FROM audit_log WHERE event='STUDENT_BOOKING_AUTO_CANCEL'",
        ).fetchone()[0]
        cnt_sum = conn.execute(
            "SELECT COUNT(1) FROM audit_log WHERE event='TEACHER_SLOT_CANCEL_NOTIFY'",
        ).fetchone()[0]
    assert st == "canceled" and enr == "canceled"
    assert cnt_auto >= 1 and cnt_sum >= 1
    # Bot delivered at least one message to student tg
    assert any(chat_id == "s-e2e" for chat_id, _ in bot.sent)
