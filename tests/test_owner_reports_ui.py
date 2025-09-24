import csv
import importlib
import io
import json
import time
from datetime import datetime, timezone
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _apply_migrations(names: list[str]) -> None:
    import app.db.conn as conn

    for name in names:
        with open(f"migrations/{name}", "r", encoding="utf-8") as f:
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
        self._answers: list[tuple[str, Any, str | None]] = []
        self._docs: list[tuple[Any, str | None]] = []

    async def answer(
        self, text: str, reply_markup: Any = None, parse_mode: str | None = None
    ) -> None:
        self._answers.append((text, reply_markup, parse_mode))

    async def answer_document(self, document: Any, caption: str | None = None) -> None:
        self._docs.append((document, caption))

    async def edit_text(
        self, text: str, reply_markup: Any = None, parse_mode: str | None = None
    ) -> None:
        self._answers.append((text, reply_markup, parse_mode))

    async def edit_reply_markup(self, reply_markup: Any = None) -> None:
        self._answers.append(("", reply_markup, None))

    @property
    def docs(self) -> list[tuple[Any, str | None]]:
        return self._docs

    @property
    def texts(self) -> list[str]:
        return [t for t, _, _ in self._answers if t]


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


def _identity(user_id: str, tg_id: str, role: str = "owner", name: str | None = None):
    from app.core.auth import Identity

    return Identity(id=user_id, role=role, tg_id=tg_id, name=name)


def _decode_csv(document) -> list[list[str]]:
    text = document.data.decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.asyncio
async def test_owner_report_audit_exports_human_and_csv(monkeypatch):
    from app.core import callbacks
    from app.db.conn import db

    _apply_migrations(["002_epic5_users_assignments.sql"])
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    monkeypatch.setattr(owner, "backup_recent", lambda *_, **__: True)

    now = int(time.time())
    owner_id = "owner-1"
    student_id = "student-1"

    with db() as conn:
        conn.execute(
            "INSERT INTO users(id, tg_id, role, name, created_at_utc, updated_at_utc, email, group_name, tef, capacity, is_active) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                owner_id,
                "700",
                "owner",
                "Owner One",
                now,
                now,
                None,
                None,
                None,
                None,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO users(id, tg_id, role, name, created_at_utc, updated_at_utc, email, group_name, tef, capacity, is_active) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                student_id,
                "100",
                "student",
                "Student One",
                now,
                now,
                "student@example.com",
                "G-1",
                None,
                None,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO audit_log(ts_utc, request_id, actor_id, as_user_id, as_role, event, object_type, object_id, meta_json) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                now,
                "req-1",
                owner_id,
                student_id,
                "student",
                "OWNER_MATERIAL_UPLOAD",
                "material",
                42,
                json.dumps({"week": 3, "type": "p", "size_bytes": 1024}),
            ),
        )
        conn.commit()

    user = StubUser(700, full_name="Owner")
    message = StubMessage(user)
    cb = callbacks.build("own", {"action": "rep_audit"}, role="owner")

    await owner.ownui_reports_audit(
        StubCallbackQuery(cb, user, message),
        _identity(owner_id, "700", name="Owner One"),
    )

    docs = message.docs
    assert len(docs) == 2
    (human_doc, human_caption), (csv_doc, csv_caption) = docs

    assert human_doc.filename.endswith("_human.txt")
    human_text = human_doc.data.decode("utf-8")
    assert "Владелец Owner One" in human_text
    assert "Неделя: 3" in human_text
    assert "Тип: Домашние материалы" in human_text

    assert csv_doc.filename.endswith(".csv")
    csv_rows = _decode_csv(csv_doc)
    assert csv_rows[0] == [
        "entry_id",
        "ts_iso",
        "event",
        "actor_id",
        "actor_name",
        "actor_role",
        "actor_tg",
        "as_user_id",
        "as_name",
        "as_role",
        "as_tg",
        "object_type",
        "object_id",
        "request_id",
        "meta",
    ]
    assert csv_rows[1][2] == "OWNER_MATERIAL_UPLOAD"
    assert "week=3" in csv_rows[1][-1]
    assert "size_bytes=1.0 KB" in csv_rows[1][-1]

    with db() as conn:
        row = conn.execute(
            "SELECT event, meta_json FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["event"] == "OWNER_AUDIT_EXPORT"
    meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    assert meta.get("report_type") == "audit_log"
    assert meta.get("records_count") == 1


@pytest.mark.asyncio
async def test_owner_report_grades_uses_grades_table(monkeypatch):
    from app.core import callbacks
    from app.db.conn import db

    _apply_migrations(["002_epic5_users_assignments.sql", "014_grades.sql"])
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    monkeypatch.setattr(owner, "backup_recent", lambda *_, **__: True)

    now = int(time.time())
    owner_id = "owner-2"
    student_id = "student-2"

    with db() as conn:
        conn.execute(
            "INSERT INTO users(id, tg_id, role, name, created_at_utc, updated_at_utc, email, group_name, tef, capacity, is_active) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                owner_id,
                "701",
                "owner",
                "Owner Two",
                now,
                now,
                None,
                None,
                None,
                None,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO users(id, tg_id, role, name, created_at_utc, updated_at_utc, email, group_name, tef, capacity, is_active) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                student_id,
                "200",
                "student",
                "Student Two",
                now,
                now,
                "student2@example.com",
                "G-2",
                None,
                None,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO weeks(week_no, title, created_at_utc) VALUES(?,?,?)",
            (2, "W2", now),
        )
        conn.execute(
            "INSERT INTO grades(student_id, week_no, score_int, graded_by, graded_at_utc, prev_score_int, comment, origin) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                student_id,
                2,
                9,
                owner_id,
                now,
                None,
                None,
                "slot",
            ),
        )
        conn.commit()

    user = StubUser(701, full_name="Owner")
    message = StubMessage(user)
    cb = callbacks.build("own", {"action": "rep_grades"}, role="owner")

    await owner.ownui_reports_grades(
        StubCallbackQuery(cb, user, message),
        _identity(owner_id, "701", name="Owner Two"),
    )

    docs = message.docs
    assert len(docs) == 1
    document, caption = docs[0]
    assert document.filename.startswith("grades_")
    rows = _decode_csv(document)
    assert rows[0] == ["student", "group", "email", "week", "grade"]
    assert rows[1] == [
        "Student Two",
        "G-2",
        "student2@example.com",
        "W02",
        "9",
    ]

    with db() as conn:
        row = conn.execute(
            "SELECT event, meta_json FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["event"] == "OWNER_REPORT_EXPORT"
    meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    assert meta.get("type") == "grades_csv"
    assert meta.get("records_count") == 1


@pytest.mark.asyncio
async def test_owner_report_course_exports_init_csv(monkeypatch):
    from app.core import callbacks
    from app.db.conn import db

    _apply_migrations(
        [
            "002_epic5_users_assignments.sql",
            "004_course_weeks_schema.sql",
            "010_course_tz.sql",
        ]
    )
    _install_aiogram_stub(monkeypatch)
    from app.bot import ui_owner_stub as owner

    importlib.reload(owner)

    monkeypatch.setattr(owner, "backup_recent", lambda *_, **__: True)

    now = int(time.time())
    owner_id = "owner-3"

    deadline_ts = int(datetime(2025, 1, 1, 23, 59, tzinfo=timezone.utc).timestamp())

    with db() as conn:
        conn.execute(
            "INSERT INTO users(id, tg_id, role, name, created_at_utc, updated_at_utc, email, group_name, tef, capacity, is_active) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                owner_id,
                "702",
                "owner",
                "Owner Three",
                now,
                now,
                None,
                None,
                None,
                None,
                1,
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO course(id, name, created_at_utc, updated_at_utc, tz) VALUES(1, ?, ?, ?, ?)",
            ("Physics", now, now, "UTC"),
        )
        conn.execute(
            "INSERT INTO weeks(week_no, title, created_at_utc, topic, description, deadline_ts_utc) VALUES(?,?,?,?,?,?)",
            (1, "W1", now, "Intro", "Basics", deadline_ts),
        )
        conn.execute(
            "INSERT INTO weeks(week_no, title, created_at_utc, topic, description, deadline_ts_utc) VALUES(?,?,?,?,?,NULL)",
            (2, "W2", now, "Vectors", "Vector algebra"),
        )
        conn.commit()

    user = StubUser(702, full_name="Owner")
    message = StubMessage(user)
    cb = callbacks.build("own", {"action": "rep_course"}, role="owner")

    await owner.ownui_reports_course(
        StubCallbackQuery(cb, user, message),
        _identity(owner_id, "702", name="Owner Three"),
    )

    docs = message.docs
    assert len(docs) == 1
    document, caption = docs[0]
    assert document.filename.startswith("course_")
    assert caption is not None
    assert "Physics" in caption
    assert "TZ: UTC" in caption

    rows = _decode_csv(document)
    assert rows[0] == ["week_id", "topic", "description", "deadline"]
    assert rows[1] == ["W01", "Intro", "Basics", "2025-01-01"]
    assert rows[2] == ["W02", "Vectors", "Vector algebra", ""]

    with db() as conn:
        row = conn.execute(
            "SELECT event, meta_json FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["event"] == "OWNER_REPORT_EXPORT"
    meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    assert meta.get("type") == "course_csv"
    assert meta.get("records_count") == 2
