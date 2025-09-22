import asyncio
import importlib
import time


def _apply_base_migrations():
    import app.db.conn as conn

    migs = [
        "migrations/004_course_weeks_schema.sql",
        "migrations/009_slots_location.sql",
        "migrations/010_course_tz.sql",
        "migrations/011_users_tz.sql",
    ]
    for path in migs:
        with open(path, "r", encoding="utf-8") as f:
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
    def answers(self) -> list[tuple[str, object, str | None]]:
        return self._answers


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


def test_manual_time_buttons_show_teacher_local(monkeypatch, db_tmpdir):
    from app.core import callbacks
    from app.db.conn import db

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO course(id, name, created_at_utc, updated_at_utc, tz) VALUES(1, ?, ?, ?, ?)",
            ("Course", now, now, "UTC"),
        )
        conn.execute(
            "UPDATE users SET tz=? WHERE tg_id=?",
            ("Europe/Moscow", "123"),
        )
        conn.commit()

    user = StubUser(123, full_name="Teacher")
    ident = _identity("123", role="teacher")

    teacher._manual_ctx_put(user.id, {"y": 2025, "m": 1, "d": 2})

    cb_start = callbacks.build(
        "t",
        {"action": "sch_manual_time_start", "part": "morning", "p": 0},
        role="teacher",
    )
    m_start = StubMessage(user)
    cq_start = StubCallbackQuery(cb_start, user, m_start)
    _run(teacher.tui_sch_manual_time_start(cq_start, ident))
    kb_start = m_start.markups[-1]
    first_label = kb_start.inline_keyboard[0][0].text
    assert first_label == "08:00 (у вас 11:00)"

    teacher._manual_ctx_put(user.id, {"sh": 21, "sm": 50})

    cb_end = callbacks.build(
        "t", {"action": "sch_manual_time_end", "p": 0}, role="teacher"
    )
    m_end = StubMessage(user)
    cq_end = StubCallbackQuery(cb_end, user, m_end)
    _run(teacher.tui_sch_manual_time_end(cq_end, ident))
    kb_end = m_end.markups[-1]
    end_label = kb_end.inline_keyboard[0][0].text
    assert end_label == "22:00 (у вас 01:00+1)"


def test_step2_online_auto_advance_and_validation(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123, full_name="Teacher")
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    cb_place = callbacks.build(
        "t", {"action": "sch_manual_place", "mode": "online"}, role="teacher"
    )
    _run(teacher.tui_sch_manual_place(StubCallbackQuery(cb_place, user, m), ident))
    kb = m.markups[-1]
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert all("Далее" not in t for t in texts)

    m_bad = StubMessage(user)
    m_bad.text = "not-a-url"  # type: ignore[attr-defined]
    _run(teacher.tui_manual_receive_location(m_bad, ident))
    assert any("Некорректный URL" in t for t, _, _ in m_bad.answers)

    m_ok = StubMessage(user)
    m_ok.text = "https://example.com/meet"  # type: ignore[attr-defined]
    _run(teacher.tui_manual_receive_location(m_ok, ident))
    assert any("Шаг 3/7 — дата" in t for t, _, _ in m_ok.answers)


def test_step2_offline_default_and_toast(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123, full_name="Teacher")
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    cb_place = callbacks.build(
        "t", {"action": "sch_manual_place", "mode": "offline"}, role="teacher"
    )
    _run(teacher.tui_sch_manual_place(StubCallbackQuery(cb_place, user, m), ident))
    txt = m.answers[-1][0]
    assert "<b>Аудитория:</b> <i>по расписанию (по умолчанию)</i>" in txt
    kb = m.markups[-1]
    assert any(b.text == "Далее" for row in kb.inline_keyboard for b in row)

    m_empty = StubMessage(user)
    m_empty.text = ""  # type: ignore[attr-defined]
    _run(teacher.tui_manual_receive_location(m_empty, ident))

    cb_date = callbacks.build("t", {"action": "sch_manual_date"}, role="teacher")
    cq_date = StubCallbackQuery(cb_date, user, m)
    _run(teacher.tui_sch_manual_date(cq_date, ident))
    if not any("Аудитория сохранена" in t for t, _ in cq_date.alerts):
        assert any("Шаг 3/7 — дата" in t for t, _, _ in m.answers)


def test_time_end_capped(monkeypatch, db_tmpdir):
    from app.core import callbacks, state_store

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")
    state_store.put_at(
        f"t_manual_ctx:{user.id}",
        "t_manual",
        {"y": 2025, "m": 1, "d": 1, "sh": 19, "sm": 0},
        ttl_sec=900,
    )
    all_times = []
    page = 0
    while True:
        cb_end = callbacks.build(
            "t", {"action": "sch_manual_time_end", "p": page}, role="teacher"
        )
        _run(teacher.tui_sch_manual_time_end(StubCallbackQuery(cb_end, user, m), ident))
        kb = m.markups[-1]
        btns = [b.text for row in kb.inline_keyboard for b in row]
        all_times += [t for t in btns if t and ":" in t]
        if not any(t == "Вперёд »" for t in btns):
            break
        page += 1
    assert all_times[-1] == "23:50"


def test_duration_and_capacity_options(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    cb_dur = callbacks.build("t", {"action": "sch_manual_duration"}, role="teacher")
    _run(teacher.tui_sch_manual_duration(StubCallbackQuery(cb_dur, user, m), ident))
    texts = [b.text for row in m.markups[-1].inline_keyboard for b in row]
    assert {"10", "15", "20", "90", "Больше…"}.issubset(set(texts))

    cb_place_on = callbacks.build(
        "t", {"action": "sch_manual_place", "mode": "online"}, role="teacher"
    )
    _run(teacher.tui_sch_manual_place(StubCallbackQuery(cb_place_on, user, m), ident))
    cb_cap = callbacks.build(
        "t", {"action": "sch_manual_capacity", "dur": 20}, role="teacher"
    )
    _run(teacher.tui_sch_manual_capacity(StubCallbackQuery(cb_cap, user, m), ident))
    texts = [b.text for row in m.markups[-1].inline_keyboard for b in row]
    assert set(texts[:3]) == {"1", "2", "3"}

    cb_place_off = callbacks.build(
        "t", {"action": "sch_manual_place", "mode": "offline"}, role="teacher"
    )
    _run(teacher.tui_sch_manual_place(StubCallbackQuery(cb_place_off, user, m), ident))
    _run(teacher.tui_sch_manual_capacity(StubCallbackQuery(cb_cap, user, m), ident))
    texts = [b.text for row in m.markups[-1].inline_keyboard for b in row]
    assert any("Все желающие" in t for t in texts)
    assert any("Точное значение…" in t for t in texts)


def test_create_persists_mode_and_location(monkeypatch, db_tmpdir):
    from app.core import callbacks, state_store
    from app.db.conn import db

    _apply_base_migrations()
    _install_aiogram_stub(monkeypatch)
    _seed_teacher()
    from app.bot import ui_teacher_stub as teacher

    importlib.reload(teacher)

    user = StubUser(123)
    m = StubMessage(user)
    ident = _identity("123", role="teacher")

    state_store.put_at(
        f"t_manual_ctx:{user.id}",
        "t_manual",
        {
            "mode": "offline",
            "location": "Ауд. 101",
            "y": 2025,
            "m": 1,
            "d": 1,
            "sh": 10,
            "sm": 0,
            "eh": 11,
            "em": 0,
            "dur": 30,
            "cap": 2,
        },
        ttl_sec=900,
    )
    cb_create = callbacks.build("t", {"action": "sch_manual_create"}, role="teacher")
    cq = StubCallbackQuery(cb_create, user, m)
    _run(teacher.tui_sch_manual_create(cq, ident))
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(1), MIN(mode), MIN(location) FROM slots",
        ).fetchone()
        assert int(row[0]) >= 2
        assert row[1] == "offline"
        assert row[2] == "Ауд. 101"
