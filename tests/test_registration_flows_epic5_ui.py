import importlib
from typing import Any, Optional

import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def _apply_epic5_migration():
    import app.db.conn as conn

    with open("migrations/002_epic5_users_assignments.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with conn.db() as c:
        c.executescript(sql)
        c.commit()


def _insert_user(
    role: str,
    name: str,
    email: Optional[str] = None,
    group_name: Optional[str] = None,
    tg_id: Optional[str] = None,
    tef: Optional[int] = None,
    capacity: Optional[int] = None,
):
    import app.db.conn as conn

    with conn.db() as c:
        c.execute(
            (
                "INSERT INTO users(tg_id, role, name, email, group_name, tef, capacity, created_at_utc, updated_at_utc) "
                "VALUES(?,?,?,?,?,?,?, strftime('%s','now'), strftime('%s','now'))"
            ),
            (tg_id, role, name, email, group_name, tef, capacity),
        )
        c.commit()


class StubUser:
    def __init__(self, uid: int, full_name: Optional[str] = None):
        self.id = uid
        self.full_name = full_name or ""


class StubMessage:
    def __init__(self, from_user: StubUser):
        self.from_user = from_user
        self._answers: list[tuple[str, Any]] = []

    async def answer(self, text: str, reply_markup: Any = None):
        self._answers.append((text, reply_markup))

    @property
    def texts(self) -> list[str]:
        return [t for t, _ in self._answers]


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


def _set_cfg(
    course_secret: Optional[str] = None,
    owners: Optional[list[str]] = None,
    override: Optional[str] = None,
):
    import app.core.config as config

    importlib.reload(config)
    if course_secret is not None:
        config.cfg.course_secret = course_secret
    if owners is not None:
        config.cfg.telegram_owner_ids_raw = ",".join(owners)
    if override is not None:
        config.cfg.auth_tg_override = override


async def _owner_flow_yes_capacity_tgname(capacity_value: int = 8):
    # Apply DB and config first, then (re)load module to ensure it sees cfg
    _apply_epic5_migration()
    _set_cfg(course_secret="sec", owners=["100"], override="")
    import importlib

    from app.bot import commands_epic5_register_owner as owner
    from app.core import callbacks

    importlib.reload(owner)

    m = StubMessage(StubUser(100, full_name="Owner Name"))
    await owner.owner_start(m, _identity("100", role="guest"))
    assert any("владелец курса" in t for t in m.texts)
    cb = callbacks.build("ownreg", {})
    cq = StubCallbackQuery(cb, m.from_user, m)
    await owner.owner_reg_start(cq, _identity("100", role="guest"))
    assert any("Хотите также работать как преподаватель" in t for t in m.texts)
    cb_yes = callbacks.build("ownyes", {})
    cq2 = StubCallbackQuery(cb_yes, m.from_user, m)
    await owner.owner_yes_teacher(cq2, _identity("100", role="owner"))
    assert any("вместимость" in t.lower() for t in m.texts)
    cb_cap = callbacks.build("owncap", {"v": capacity_value})
    cq3 = StubCallbackQuery(cb_cap, m.from_user, m)
    await owner.owner_capacity_pick(cq3, _identity("100", role="owner"))
    assert any("Настроена вместимость" in t for t in m.texts)
    assert any("Как отобразить ваше имя" in t for t in m.texts)
    cb_tg = callbacks.build("owntg", {})
    cq4 = StubCallbackQuery(cb_tg, m.from_user, m)
    await owner.owner_name_use_tg(cq4, _identity("100", role="owner"))
    assert any(t.strip() == "✅ Регистрация завершена" for t in m.texts)


@pytest.mark.asyncio
async def test_owner_full_flow_yes_capacity_tgname():
    await _owner_flow_yes_capacity_tgname(8)


@pytest.mark.asyncio
async def test_owner_flow_no_teacher_manual_name():
    _apply_epic5_migration()
    _set_cfg(course_secret="sec", owners=["200"], override="")
    import importlib

    from app.bot import commands_epic5_register_owner as owner
    from app.core import callbacks

    importlib.reload(owner)

    m = StubMessage(StubUser(200, full_name="TG Name"))
    await owner.owner_start(m, _identity("200", role="guest"))
    cb = callbacks.build("ownreg", {})
    await owner.owner_reg_start(
        StubCallbackQuery(cb, m.from_user, m), _identity("200", role="guest")
    )
    cb_no = callbacks.build("ownno", {})
    await owner.owner_no_teacher(
        StubCallbackQuery(cb_no, m.from_user, m), _identity("200", role="owner")
    )
    cb_nm = callbacks.build("ownnm", {})
    await owner.owner_name_ask(
        StubCallbackQuery(cb_nm, m.from_user, m), _identity("200", role="owner")
    )

    class TextMsg(StubMessage):
        def __init__(self, base: StubMessage, text: str):
            super().__init__(base.from_user)
            self.text = text

    tmsg = TextMsg(m, "Иванов Иван")
    await owner.owner_name_set(tmsg, _identity("200", role="owner"))
    assert any("Имя установлено" in t for t in tmsg.texts)
    assert any(t.strip() == "✅ Регистрация завершена" for t in tmsg.texts)


def _prep_teachers():
    _apply_epic5_migration()
    _insert_user(
        "teacher", "Bound T", email="t1@example.com", tg_id="tg-bound", capacity=3
    )
    _insert_user("teacher", "Free T", email="t2@example.com", tg_id=None, capacity=2)


@pytest.mark.asyncio
async def test_teacher_flow_list_all_and_bound_error_and_confirm():
    from app.bot import commands_epic5_register as reg
    from app.core import callbacks
    from app.core.config import cfg

    _prep_teachers()
    cfg.course_secret = "s3cr3t"
    m = StubMessage(StubUser(300, full_name="TeacherUser"))
    cb_trole = callbacks.build("reg_t_role", {})
    await reg.reg_teacher(
        StubCallbackQuery(cb_trole, m.from_user, m), _identity("300", role="guest")
    )

    class TextMsg(StubMessage):
        def __init__(self, base: StubMessage, text: str):
            super().__init__(base.from_user)
            self.text = text

    t1 = TextMsg(m, "bad")
    await reg.reg_input_text(t1, _identity("300", role="guest"))
    assert any("Неверный код" in t for t in t1.texts)
    t2 = TextMsg(m, "s3cr3t")
    await reg.reg_input_text(t2, _identity("300", role="guest"))
    # The list is sent via the same message object that was passed to handler (t2)
    assert any("Выберите профиль преподавателя" in t for t in t2.texts)

    from app.db import repo_users

    cands = repo_users.find_all_teachers_for_bind()
    bound = next(x for x in cands if x.get("tg_id"))
    free = next(x for x in cands if not x.get("tg_id"))
    cb_pick_bound = callbacks.build("reg_pick", {"uid": bound["id"]})
    await reg.reg_pick(
        StubCallbackQuery(cb_pick_bound, m.from_user, m), _identity("300", role="guest")
    )
    assert any("уже зарегистрирован" in t for t in m.texts)

    cb_pick_free = callbacks.build("reg_pick", {"uid": free["id"]})
    await reg.reg_pick(
        StubCallbackQuery(cb_pick_free, m.from_user, m), _identity("300", role="guest")
    )
    cb_cy = callbacks.build("reg_confirm_yes", {})
    await reg.reg_confirm(
        StubCallbackQuery(cb_cy, m.from_user, m), _identity("300", role="guest")
    )
    assert any("Регистрация завершена" in t for t in m.texts) or any(
        "привязаны к профилю" in t for t in m.texts
    )


@pytest.mark.asyncio
async def test_student_flow_email_bound_and_card_confirm():
    from app.bot import commands_epic5_register as reg
    from app.core import callbacks

    # from app.core.config import cfg

    _apply_epic5_migration()
    _insert_user(
        "student", "Alice", email="alice@ex.com", group_name="IU1-01", tg_id="tgA"
    )
    _insert_user("student", "Bob", email="bob@ex.com", group_name="IU1-02", tg_id=None)
    m = StubMessage(StubUser(400, full_name="StudentUser"))
    cb_srole = callbacks.build("reg_s_role", {})
    await reg.reg_student(
        StubCallbackQuery(cb_srole, m.from_user, m), _identity("400", role="guest")
    )

    class TextMsg(StubMessage):
        def __init__(self, base: StubMessage, text: str):
            super().__init__(base.from_user)
            self.text = text

    t1 = TextMsg(m, "alice@ex.com")
    await reg.reg_input_text(t1, _identity("400", role="guest"))
    # Error about already bound email is answered to the same message (t1)
    assert any("уже привязан" in t for t in t1.texts)
    t2 = TextMsg(m, "bob@ex.com")
    await reg.reg_input_text(t2, _identity("400", role="guest"))
    # The student card is sent via the same message object that was passed (t2)
    assert any("Найден профиль студента" in t for t in t2.texts)
    cb_cy = callbacks.build("reg_confirm_yes", {})
    await reg.reg_confirm(
        StubCallbackQuery(cb_cy, m.from_user, m), _identity("400", role="guest")
    )
    assert any("Регистрация завершена" in t for t in m.texts)
