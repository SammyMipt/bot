import asyncio
import importlib
import io
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
        photo = object()

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
        self.bot = None

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
    def texts(self) -> list[str]:
        return [t for t, _, _ in self._answers]


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


def _identity(tg_id: str, role: str = "student", name: str | None = None):
    from app.core.auth import Identity
    from app.db.conn import db

    with db() as conn:
        row = conn.execute(
            "SELECT id, name FROM users WHERE tg_id=?", (tg_id,)
        ).fetchone()
        uid = row[0] if row else ""
        nm = name if name is not None else (row[1] if row else None)
    return Identity(id=uid, role=role, tg_id=tg_id, name=nm)


def _run(awaitable):
    return asyncio.run(awaitable)


def _apply_weeks_and_students_submissions_migrations():
    import app.db.conn as conn

    # Ensure weeks has the required fields
    with conn.db() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(weeks)").fetchall()}
        need_004 = "topic" not in cols
    if need_004:
        with open("migrations/004_course_weeks_schema.sql", "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.db() as c:
            c.executescript(sql)
            c.commit()
    # Create students_submissions
    with open("migrations/012_students_submissions.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.db() as c:
        c.executescript(sql)
        c.commit()


def _seed_student_and_week(
    name: str = "Иванов И.И.", tg_id: str = "s-2", week_no: int = 3
):
    from app.db.conn import db

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc) VALUES(?,?,?, ?, ?)",
            (tg_id, "student", name, now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(?, ?, ?)",
            (week_no, "Колебания", now),
        )
        conn.commit()


class _Doc:
    def __init__(self, file_id: str, file_name: str, size: int, mime: str):
        self.file_id = file_id
        self.file_name = file_name
        self.file_size = size
        self.mime_type = mime


class _Bot:
    class _File:
        def __init__(self, path: str):
            self.file_path = path

    async def get_file(self, file_id: str):
        return _Bot._File(f"/tmp/{file_id}")

    async def download_file(self, file_path: str):
        return io.BytesIO(b"test-bytes-" + file_path.encode())


def test_student_upload_human_filename_and_sequence(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _apply_weeks_and_students_submissions_migrations()
    _seed_student_and_week()

    from app.bot import ui_student_stub as student

    importlib.reload(student)

    # Open upload screen for week 3
    user = StubUser(2001, full_name="Иванов И.И.")
    m = StubMessage(user)
    m.bot = _Bot()

    cb = callbacks.build("s", {"action": "week_upload", "week": 3}, role="student")
    _run(
        student.sui_week_upload(
            StubCallbackQuery(cb, user, m),
            _identity("s-2", role="student", name="Иванов И.И."),
        )
    )

    # Send first document (pdf)
    class _Msg:
        def __init__(self, from_user, doc):
            self.from_user = from_user
            self.document = doc
            self.bot = _Bot()
            self._answers = []

        async def answer(self, text: str, reply_markup=None, parse_mode=None):
            m._answers.append((text, reply_markup, parse_mode))

    msg1 = _Msg(user, _Doc("f1", "solution1.pdf", 1024, "application/pdf"))
    _run(
        student.sui_receive_submission_doc(
            msg1, _identity("s-2", role="student", name="Иванов И.И.")
        )
    )

    # Send second document (png)
    msg2 = _Msg(user, _Doc("f2", "image2.png", 2048, "image/png"))
    _run(
        student.sui_receive_submission_doc(
            msg2, _identity("s-2", role="student", name="Иванов И.И.")
        )
    )

    # Verify DB records and filenames
    from app.db.conn import db

    with db() as conn:
        rows = conn.execute(
            (
                "SELECT path FROM students_submissions "
                "WHERE student_id=(SELECT id FROM users WHERE tg_id='s-2') "
                "AND week_no=3 AND deleted_at_utc IS NULL "
                "ORDER BY id ASC"
            )
        ).fetchall()
        paths = [r[0] for r in rows]
    assert len(paths) == 2
    assert paths[0].endswith("Иванов_Н03_1.pdf")
    assert paths[1].endswith("Иванов_Н03_2.png")


def test_student_upload_rejects_disallowed_ext(monkeypatch, db_tmpdir):
    from app.core import callbacks

    _install_aiogram_stub(monkeypatch)
    _apply_weeks_and_students_submissions_migrations()
    _seed_student_and_week(tg_id="s-3")

    from app.bot import ui_student_stub as student

    importlib.reload(student)

    # Open upload screen for week 3
    user = StubUser(2002, full_name="Иванов И.И.")
    m = StubMessage(user)
    m.bot = _Bot()
    cb = callbacks.build("s", {"action": "week_upload", "week": 3}, role="student")
    _run(
        student.sui_week_upload(
            StubCallbackQuery(cb, user, m),
            _identity("s-3", role="student", name="Иванов И.И."),
        )
    )

    # Try to send DOCX (disallowed)
    class _Msg:
        def __init__(self, from_user, doc):
            self.from_user = from_user
            self.document = doc
            self.bot = _Bot()
            self._answers = []

        async def answer(self, text: str, reply_markup=None, parse_mode=None):
            m._answers.append((text, reply_markup, parse_mode))

    msg = _Msg(
        user,
        _Doc(
            "f3",
            "report.docx",
            1200,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    )
    _run(
        student.sui_receive_submission_doc(
            msg, _identity("s-3", role="student", name="Иванов И.И.")
        )
    )

    assert any("Неподдерживаемый тип файла" in t for t in m.texts)
    # Ensure nothing inserted
    from app.db.conn import db

    with db() as conn:
        cnt = conn.execute(
            (
                "SELECT COUNT(1) FROM students_submissions "
                "WHERE student_id=(SELECT id FROM users WHERE tg_id='s-3') "
                "AND week_no=3 AND deleted_at_utc IS NULL"
            )
        ).fetchone()[0]
    assert int(cnt) == 0
