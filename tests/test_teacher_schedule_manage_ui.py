import asyncio
import importlib
import time
from datetime import datetime, timezone


def _apply_base_migrations():
    import app.db.conn as conn

    with open("migrations/009_slots_location.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.db() as c:
        try:
            c.executescript(sql)
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


def _insert_student(name: str) -> str:
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES(?,?,?, ?, ?)",
            (f"{name}-tg", "student", name, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM users WHERE name=? AND role='student'", (name,)
        ).fetchone()
        return row[0]


def _enroll(slot_id: int, student_id: str):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO slot_enrollments(slot_id, user_id, status, booked_at_utc) VALUES(?,?, 'booked', ?)",
            (slot_id, student_id, now),
        )
        conn.commit()


def test_manage_root_days_and_all(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    cb = callbacks.build("t", {"action": "sch_manage"}, role="teacher")
    _run(teacher.tui_sch_manage(StubCallbackQuery(cb, user, m), ident))
    text = m.answers[-1][0]
    kb = m.markups[-1]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "Управление расписанием" in text
    assert any("🗓 Все слоты" == t for t in labels)
    assert any(
        ", " in t
        and any(
            mon in t
            for mon in [
                "янв",
                "фев",
                "мар",
                "апр",
                "май",
                "июн",
                "июл",
                "авг",
                "сен",
                "окт",
                "ноя",
                "дек",
            ]
        )
        for t in labels
    )


def test_day_list_compact_buttons(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    s1 = _utc(2025, 1, 2, 9, 0)
    s2 = _utc(2025, 1, 2, 10, 0)
    _insert_slot(s1, 30, 2, status="open", mode="online")
    _insert_slot(s2, 30, 3, status="open", mode="offline")

    cb = callbacks.build(
        "t", {"action": "sch_day", "y": 2025, "m": 1, "d": 2}, role="teacher"
    )
    _run(teacher.tui_sch_day(StubCallbackQuery(cb, user, m), ident))
    text = m.answers[-1][0]
    kb = m.markups[-1]
    assert "Всего слотов: <b>2</b>" in text
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("👥" in t for t in labels)


def test_all_slots_pagination_and_header(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    start = int(time.time()) + 3600
    for i in range(12):
        _insert_slot(start + i * 1800, 30, 2, status="open")

    cb0 = callbacks.build("t", {"action": "sch_manage_all", "p": 0}, role="teacher")
    _run(teacher.tui_sch_manage_all(StubCallbackQuery(cb0, user, m), ident))
    text0 = m.answers[-1][0]
    assert "Всего: <b>12</b>" in text0 and "Стр. <b>1</b>/" in text0

    cb1 = callbacks.build("t", {"action": "sch_manage_all", "p": 1}, role="teacher")
    _run(teacher.tui_sch_manage_all(StubCallbackQuery(cb1, user, m), ident))
    text1 = m.answers[-1][0]
    assert "Стр. <b>2</b>" in text1


def test_slot_card_toggle_and_delete_cancel(monkeypatch, db_tmpdir):
    from app.core import callbacks
    from app.db.conn import db

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    sid = _insert_slot(int(time.time()) + 7200, 30, 2, status="open")

    cb_open = callbacks.build("t", {"action": "sch_slot", "id": sid}, role="teacher")
    cq = StubCallbackQuery(cb_open, user, m)
    _run(teacher.tui_sch_slot(cq, ident))

    cb_tgl = callbacks.build(
        "t", {"action": "sch_slot_toggle", "id": sid}, role="teacher"
    )
    _run(teacher.tui_sch_slot_toggle(StubCallbackQuery(cb_tgl, user, m), ident))
    with db() as conn:
        st = conn.execute("SELECT status FROM slots WHERE id=?", (sid,)).fetchone()[0]
        assert st == "closed"

    cb_delq = callbacks.build(
        "t", {"action": "sch_slot_delq", "id": sid}, role="teacher"
    )
    _run(teacher.tui_sch_slot_delq(StubCallbackQuery(cb_delq, user, m), ident))
    cb_cancel = callbacks.build(
        "t", {"action": "sch_slot_del_cancel", "id": sid}, role="teacher"
    )
    cq2 = StubCallbackQuery(cb_cancel, user, m)
    _run(teacher.tui_sch_slot_del_cancel(cq2, ident))
    assert any("Удаление отменено" in t for t, _ in cq2.alerts)


def test_slot_students_listing_and_card(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    sid = _insert_slot(int(time.time()) + 7200, 30, 3, status="open")
    s1 = _insert_student("Alice")
    s2 = _insert_student("Bob")
    _enroll(sid, s1)
    _enroll(sid, s2)

    cb_list = callbacks.build(
        "t", {"action": "sch_slot_students", "id": sid}, role="teacher"
    )
    _run(teacher.tui_sch_slot_students(StubCallbackQuery(cb_list, user, m), ident))
    kb = m.markups[-1]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert sum(1 for t in labels if t.startswith("👤 ")) >= 2

    cb_card = callbacks.build(
        "t", {"action": "sch_slot_student", "sid": sid, "uid": s1}, role="teacher"
    )
    _run(teacher.tui_sch_slot_student(StubCallbackQuery(cb_card, user, m), ident))
    assert any("Карточка студента" in t for t, _, _ in m.answers)
