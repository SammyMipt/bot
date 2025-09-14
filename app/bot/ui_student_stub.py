from __future__ import annotations

import html

from aiogram import Router, types
from aiogram.filters import Command

from app.core import callbacks, state_store
from app.core.auth import Identity
from app.core.repos_epic4 import list_weeks_with_titles
from app.db.conn import db
from app.services.common.time_service import format_datetime, get_course_tz, utc_now_ts

router = Router(name="ui.student.stub")


def _uid(x: types.Message | types.CallbackQuery) -> int:
    return x.from_user.id


# ------- Error mapping (aligned with docs) -------

ERROR_MESSAGES: dict[str, str] = {
    "E_INPUT_INVALID": "⛔ Некорректный ввод",
    "E_ACCESS_DENIED": "⛔ Доступ запрещён",
    "E_STATE_INVALID": "⛔ Некорректное состояние запроса",
    "E_STATE_EXPIRED": "⛔ Сессия истекла. Начните заново.",
    "E_NOT_FOUND": "❌ Не найдено",
}


async def _toast_error(
    cq: types.CallbackQuery, code: str, default_message: str | None = None
) -> None:
    msg = ERROR_MESSAGES.get(code, default_message or "⛔ Произошла ошибка")
    await cq.answer(msg, show_alert=True)


def cb(action: str, params: dict | None = None) -> str:
    payload = {"action": action}
    if params:
        payload.update(params)
    return callbacks.build("s", payload, role="student")


def _is(actions: set[str]):
    def _f(cq: types.CallbackQuery) -> bool:
        try:
            op, key = callbacks.parse(cq.data)
            if op != "s":
                return False
            _, payload = state_store.get(key)
            return payload.get("action") in actions
        except Exception:
            return False

    return _f


def _nav_key(uid: int) -> str:
    return f"s_nav:{uid}"


def _stack_get(uid: int) -> list[dict]:
    try:
        action, st = state_store.get(_nav_key(uid))
        if action != "s_nav":
            return []
        return st.get("stack") or []
    except Exception:
        return []


def _stack_set(uid: int, stack: list[dict]) -> None:
    state_store.put_at(_nav_key(uid), "s_nav", {"stack": stack}, ttl_sec=900)


def _stack_push(uid: int, screen: str, params: dict | None = None) -> None:
    st = _stack_get(uid)
    st.append({"s": screen, "p": params or {}})
    _stack_set(uid, st)


def _stack_pop(uid: int) -> dict | None:
    st = _stack_get(uid)
    if not st:
        return None
    st.pop()
    _stack_set(uid, st)
    return st[-1] if st else None


def _stack_reset(uid: int) -> None:
    _stack_set(uid, [{"s": "home", "p": {}}])


def _nav_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="⬅️ Назад", callback_data=cb("back")),
                types.InlineKeyboardButton(
                    text="🏠 Главное меню", callback_data=cb("home")
                ),
            ]
        ]
    )


def _main_menu_kb() -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="📘 Работа с неделями", callback_data=cb("weeks")
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📅 Мои записи", callback_data=cb("my_bookings")
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📊 Мои оценки", callback_data=cb("my_grades")
            )
        ],
        [types.InlineKeyboardButton(text="📜 История", callback_data=cb("history"))],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _weeks_kb(page: int = 0, per_page: int = 10) -> types.InlineKeyboardMarkup:
    items = list_weeks_with_titles(limit=100)
    weeks = sorted(items, key=lambda x: x[0])
    total_pages = max(1, (len(weeks) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = weeks[start : start + per_page]
    rows: list[list[types.InlineKeyboardButton]] = []
    for wno, title in chunk:
        label = f"W{wno:02d} — {title}"
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=label, callback_data=cb("week_menu", {"week": int(wno)})
                )
            ]
        )
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад", callback_data=cb("weeks_page", {"p": page - 1})
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »", callback_data=cb("weeks_page", {"p": page + 1})
            )
        )
    if nav:
        rows.append(nav)
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _week_menu_kb(week: int) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="ℹ️ Описание и дедлайн",
                callback_data=cb("week_info", {"week": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📖 Домашние задачи и материалы",
                callback_data=cb("week_prep", {"week": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📝 Конспекты", callback_data=cb("week_notes", {"week": week})
            ),
            types.InlineKeyboardButton(
                text="📊 Презентации", callback_data=cb("week_slides", {"week": week})
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="🎥 Записи лекций", callback_data=cb("week_video", {"week": week})
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📤 Загрузить решение",
                callback_data=cb("week_upload", {"week": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="⏰ Записаться на сдачу",
                callback_data=cb("week_book", {"week": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="❌ Отменить запись",
                callback_data=cb("week_unbook", {"week": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="✅ Узнать оценку", callback_data=cb("week_grade", {"week": week})
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# ------- Entry points -------


@router.message(Command("student"))
async def student_menu_cmd(m: types.Message, actor: Identity):
    if actor.role != "student":
        return await m.answer("⛔ Доступ запрещён.")
    uid = _uid(m)
    _stack_reset(uid)
    await m.answer("Главное меню студента", reply_markup=_main_menu_kb())


@router.message(Command("student_menu"))
async def student_menu_alt_cmd(m: types.Message, actor: Identity):
    if actor.role != "student":
        return await m.answer("⛔ Доступ запрещён.")
    uid = _uid(m)
    _stack_reset(uid)
    await m.answer("Главное меню студента", reply_markup=_main_menu_kb())


@router.callback_query(_is({"home"}))
async def sui_home(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role="student")
    except Exception:
        # Silent on expired state for idempotent top-level navigation
        pass
    _stack_reset(_uid(cq))
    text = "Главное меню студента"
    kb = _main_menu_kb()
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(_is({"back"}))
async def sui_back(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
    prev = _stack_pop(_uid(cq))  # pop current
    prev = _stack_pop(_uid(cq)) if prev else None  # pop to previous
    # If nothing — go home
    if not prev:
        return await sui_home(cq, actor)
    s = str(prev.get("s", ""))
    p = dict(prev.get("p") or {})
    if s == "weeks":
        return await sui_weeks(cq, actor)
    if s == "week_menu":
        week = int(p.get("week", 0)) if p else 0
        if week:
            return await sui_week_menu(cq, actor, week)
    # Fallback
    return await sui_home(cq, actor)


# ------- Weeks -------


@router.callback_query(_is({"weeks"}))
async def sui_weeks(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role="student")
    except Exception:
        # Silent on expired state for idempotent top-level navigation
        pass
    text = "📘 Работа с неделями\nВыберите неделю:"
    kb = _weeks_kb(0)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "weeks", {})


@router.callback_query(_is({"weeks_page"}))
async def sui_weeks_page(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role="student")
    p = int(payload.get("p", 0))
    try:
        await cq.message.edit_reply_markup(reply_markup=_weeks_kb(p))
    except Exception:
        await cq.message.answer("Выберите неделю:", reply_markup=_weeks_kb(p))
    await cq.answer()


@router.callback_query(_is({"week_menu"}))
async def sui_week_menu(
    cq: types.CallbackQuery, actor: Identity, week_no: int | None = None
):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    if week_no is None:
        _, payload = callbacks.extract(cq.data, expected_role="student")
        week_no = int(payload.get("week", 0))
    title = dict(list_weeks_with_titles(limit=200)).get(
        int(week_no), f"W{int(week_no):02d}"
    )
    text = f"W{int(week_no):02d} — {title}"
    kb = _week_menu_kb(int(week_no))
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "week_menu", {"week": int(week_no)})


# ------- Week info -------


@router.callback_query(_is({"week_info"}))
async def sui_week_info(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
        payload = {}
    week_no = int(payload.get("week", 0)) if payload else 0
    if not week_no:
        return await _toast_error(cq, "E_INPUT_INVALID")

    # Load week info
    with db() as conn:
        row = conn.execute(
            "SELECT COALESCE(topic, title, ''), COALESCE(description, ''), deadline_ts_utc FROM weeks WHERE week_no=?",
            (week_no,),
        ).fetchone()
        # Assigned teacher for this student and week (if any)
        trow = conn.execute(
            """
            SELECT COALESCE(u.name, ''), u.tg_id
            FROM teacher_student_assignments tsa
            JOIN users u ON u.id = tsa.teacher_id
            WHERE tsa.week_no = ? AND tsa.student_id = ?
            LIMIT 1
            """,
            (week_no, actor.id),
        ).fetchone()
    topic = str(row[0] or "") if row else ""
    description = str(row[1] or "") if row else ""
    deadline_ts = int(row[2]) if row and row[2] is not None else None

    # Build HTML card
    safe_topic = html.escape(topic)
    safe_desc = html.escape(description)
    header = (
        f"📘 <b>W{int(week_no):02d}</b> — {safe_topic}"
        if safe_topic
        else f"📘 <b>W{int(week_no):02d}</b>"
    )

    if deadline_ts:
        course_tz = get_course_tz()
        try:
            dt_str = format_datetime(deadline_ts, course_tz)
        except Exception:
            from app.services.common.time_service import format_date

            dt_str = format_date(deadline_ts, course_tz)
        indicator = "🟢" if deadline_ts >= utc_now_ts() else "🔴"
        deadline_line = f"⏰ <b>Дедлайн:</b> {dt_str} ({course_tz}) {indicator}"
    else:
        deadline_line = "⏰ <b>Дедлайн:</b> без дедлайна"

    parts: list[str] = [header]
    if safe_desc:
        parts.append("")
        parts.append("📝 <b>Описание</b>")
        parts.append(safe_desc)
    parts.append("")
    parts.append(deadline_line)
    # Assigned teacher line
    teacher_line: str
    if trow:
        tname = html.escape(str(trow[0] or ""))
        if not tname:
            tname = f"@{html.escape(str(trow[1] or ''))}" if trow[1] else "(без имени)"
        teacher_line = f"🧑‍🏫 <b>Принимающий преподаватель:</b> {tname}"
    else:
        teacher_line = "🧑‍🏫 <b>Принимающий преподаватель:</b> не назначен"
    parts.append(teacher_line)
    parts.append("")
    parts.append("👉 Выберите действие ниже:")

    text = "\n".join(parts)
    # Build keyboard: optional contact + nav
    rows: list[list[types.InlineKeyboardButton]] = []
    # Add contact button if we have a username-like tg_id (@username)
    try:
        if trow and trow[1] and str(trow[1]).startswith("@"):
            username = str(trow[1]).lstrip("@")
            rows.append(
                [
                    types.InlineKeyboardButton(
                        text="📬 Написать преподавателю", url=f"https://t.me/{username}"
                    )
                ]
            )
    except Exception:
        pass
    rows.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()


# ------- Stubs for week actions -------


def _dev_stub_text() -> str:
    return "Функция в разработке"


@router.callback_query(
    _is(
        {
            "week_prep",
            "week_notes",
            "week_slides",
            "week_video",
            "week_upload",
            "week_book",
            "week_unbook",
            "week_grade",
        }
    )
)
async def sui_week_action_stub(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
    try:
        await cq.message.edit_text(_dev_stub_text(), reply_markup=_nav_keyboard())
    except Exception:
        await cq.message.answer(_dev_stub_text(), reply_markup=_nav_keyboard())
    await cq.answer("Страница-заглушка")


# ------- Stubs for main menu entries -------


@router.callback_query(_is({"my_bookings", "my_grades", "history"}))
async def sui_top_level_stub(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
        payload = {}
    title_map = {
        "my_bookings": "📅 Мои записи",
        "my_grades": "📊 Мои оценки",
        "history": "📜 История",
    }
    # Read intended action for header from extracted payload
    header = title_map.get(str(payload.get("action")), "Заглушка")
    text = f"{header}\n{_dev_stub_text()}"
    kb = _nav_keyboard()
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
