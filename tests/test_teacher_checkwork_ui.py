import asyncio
import importlib
import time
from datetime import datetime, timezone


def _apply_ckw_migrations():
    import app.db.conn as conn

    for name in [
        "001_init.sql",
        "002_epic5_users_assignments.sql",
        "006_fix_tsa_types.sql",
        "009_slots_location.sql",
        "012_students_submissions.sql",
        "013_slot_enrollments_week.sql",
        "016_drop_assignments_fk_from_submissions.sql",
    ]:
        with open(f"migrations/{name}", "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.db() as c:
            try:
                c.executescript(sql)
                c.commit()
            except Exception:
                pass
    # Ensure optional columns used by code exist for tests
    with conn.db() as c:
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(weeks)").fetchall()}
            if "topic" not in cols:
                c.execute("ALTER TABLE weeks ADD COLUMN topic TEXT")
            cols_u = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
            if "group_name" not in cols_u:
                c.execute("ALTER TABLE users ADD COLUMN group_name TEXT")
            c.commit()
        except Exception:
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
        self,
        text: str,
        reply_markup: object = None,
        parse_mode: str | None = None,
        **_k,
    ):
        self._answers.append((text, reply_markup, parse_mode))

    async def edit_reply_markup(self, reply_markup: object = None, **_k):
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


def _identity(tg_id: str, role: str = "teacher"):
    from app.core.auth import Identity
    from app.db.conn import db

    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        uid = row[0] if row else ""
    return Identity(id=uid, role=role, tg_id=tg_id, name=None)


def _run(awaitable):
    return asyncio.run(awaitable)


def _seed_teacher():
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES('123','teacher','Teacher',?,?)",
            (now, now),
        )
        conn.commit()


def _get_teacher_id():
    from app.db.conn import db

    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE tg_id='123'").fetchone()
        return row[0]


def _utc(y, m, d, hh, mm):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


def _insert_week(week_no: int, title: str = ""):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(?,?,?)",
            (week_no, title, now),
        )
        conn.commit()


def _insert_slot(
    starts_at_utc: int,
    duration: int,
    cap: int,
    status: str = "open",
    mode: str | None = None,
    location: str | None = None,
) -> int:
    from app.db.conn import db

    with db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)").fetchall()}
        tid = _get_teacher_id()
        now = int(time.time())
        if "mode" in cols and "location" in cols:
            cur = conn.execute(
                (
                    "INSERT INTO slots("
                    "starts_at_utc, duration_min, capacity, status, created_by, created_at_utc, mode, location"
                    ") VALUES(?,?,?,?,?,?,?,?)"
                ),
                (starts_at_utc, duration, cap, status, tid, now, mode, location),
            )
        else:
            cur = conn.execute(
                "INSERT INTO slots(starts_at_utc, duration_min, capacity, status, created_by, created_at_utc) VALUES(?,?,?,?,?,?)",
                (starts_at_utc, duration, cap, status, tid, now),
            )
        conn.commit()
        return int(cur.lastrowid)


def _insert_student(name: str, group: str = "") -> str:
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO users(tg_id, role, name, group_name, created_at_utc, updated_at_utc) VALUES(?,?,?,?, ?, ?)",
            (f"{name}-tg", "student", name, group, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM users WHERE name=? AND role='student'", (name,)
        ).fetchone()
        return row[0]


def _assign_teacher(week_no: int, teacher_id: str, student_id: str):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO teacher_student_assignments(week_no, teacher_id, student_id, created_at_utc) VALUES(?,?,?,?)",
            (week_no, teacher_id, student_id, now),
        )
        conn.commit()


def _enroll(slot_id: int, student_id: str, week_no: int | None = None):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(slot_enrollments)").fetchall()
        }
        if "week_no" in cols and week_no is not None:
            conn.execute(
                (
                    "INSERT INTO slot_enrollments(slot_id, user_id, status, booked_at_utc, week_no) VALUES(?,?, 'booked', ?, ?)"
                ),
                (slot_id, student_id, now, week_no),
            )
        else:
            conn.execute(
                "INSERT INTO slot_enrollments(slot_id, user_id, status, booked_at_utc) VALUES(?,?, 'booked', ?)",
                (slot_id, student_id, now),
            )
        conn.commit()


def test_cw_by_date_lists_dates_and_slots(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_ckw_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    # two slots on different days
    now = int(time.time())
    _insert_slot(now + 24 * 3600, 30, 2, status="open")
    _insert_slot(now + 2 * 24 * 3600, 30, 3, status="open")

    cb = callbacks.build("t", {"action": "cw_by_date"}, role="teacher")
    _run(teacher.tui_cw_by_date(StubCallbackQuery(cb, user, m), ident))
    kb = m.markups[-1]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert sum(1 for t in labels if "." in t and t.count(".") == 2) >= 2


def test_cw_by_week_lists_students_with_group_and_card(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_ckw_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")
    tid = _get_teacher_id()

    week = 5
    _insert_week(week, "Test Week")
    s1 = _insert_student("Alice", group="G1")
    s2 = _insert_student("Bob", group="G2")
    _assign_teacher(week, tid, s1)
    _assign_teacher(week, tid, s2)

    cb0 = callbacks.build("t", {"action": "cw_by_student"}, role="teacher")
    _run(teacher.tui_cw_by_student(StubCallbackQuery(cb0, user, m), ident))
    cbw = callbacks.build("t", {"action": "cw_week_pick", "w": week}, role="teacher")
    _run(teacher.tui_cw_week_pick(StubCallbackQuery(cbw, user, m), ident))
    kb = m.markups[-1]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Alice" in t and "G1" in t for t in labels)
    assert any("Bob" in t and "G2" in t for t in labels)

    cbc = callbacks.build(
        "t", {"action": "cw_week_student", "w": week, "uid": s1}, role="teacher"
    )
    _run(teacher.tui_cw_week_student(StubCallbackQuery(cbc, user, m), ident))
    text = m.answers[-1][0]
    assert (
        "Карточка студента" in text
        and "Группа: G1" in text
        and f"Неделя: {week}" in text
    )


def test_grading_sets_grade_and_audit(monkeypatch, db_tmpdir):
    from app.core import callbacks
    from app.db.conn import db

    _apply_ckw_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    week = 7
    _insert_week(week, "Week 7")
    sid = _insert_student("Charlie", group="G7")

    cbg = callbacks.build(
        "t", {"action": "cw_grade_set", "w": week, "uid": sid, "g": 9}, role="teacher"
    )
    _run(teacher.tui_cw_grade_set(StubCallbackQuery(cbg, user, m), ident))

    with db() as conn:
        row = conn.execute(
            "SELECT status, grade, reviewed_by FROM submissions WHERE student_id=? AND week_no=? ORDER BY id DESC LIMIT 1",
            (sid, week),
        ).fetchone()
    assert row and str(row[0]) == "graded" and str(row[1]) == "9" and row[2]
