from __future__ import annotations

from aiogram.filters import Command

from aiogram import Router, types
from app.core import callbacks, state_store
from app.core.auth import Identity
from app.core.repos_epic4 import (
    list_students_with_submissions_by_week,
    list_week_submission_files_for_teacher,
    list_weeks,
)

router = Router(name="epic4.teacher")


def _cb(op: str, actions: set[str]):
    def _f(cq: types.CallbackQuery) -> bool:
        try:
            op2, key = callbacks.parse(cq.data)
            if op2 != op:
                return False
            _, payload = state_store.get(key)
            return payload.get("action") in actions
        except Exception:
            return False

    return _f


def _is_owner_or_teacher(actor: Identity) -> bool:
    return actor.role in ("owner", "teacher")


def _weeks_keyboard(role: str, page: int = 0) -> types.InlineKeyboardMarkup:
    weeks = list_weeks(limit=200)
    per_page = 28
    row_size = 7
    total_pages = max(1, (len(weeks) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = weeks[start : start + per_page]

    rows: list[list[types.InlineKeyboardButton]] = []
    row: list[types.InlineKeyboardButton] = []
    for n in chunk:
        row.append(
            types.InlineKeyboardButton(
                text=f"W{n}",
                callback_data=callbacks.build(
                    "tview", {"action": "week", "params": {"week": n}}, role=role
                ),
            )
        )
        if len(row) == row_size:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if total_pages > 1:
        nav: list[types.InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                types.InlineKeyboardButton(
                    text="« Назад",
                    callback_data=callbacks.build(
                        "tview",
                        {"action": "page", "params": {"page": page - 1}},
                        role=role,
                    ),
                )
            )
        if page < total_pages - 1:
            nav.append(
                types.InlineKeyboardButton(
                    text="Вперёд »",
                    callback_data=callbacks.build(
                        "tview",
                        {"action": "page", "params": {"page": page + 1}},
                        role=role,
                    ),
                )
            )
        if nav:
            rows.append(nav)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _students_keyboard(
    week_no: int, students: list[dict], role: str
) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    for s in students:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"Открыть {s.get('name') or s.get('tg_id')}",
                    callback_data=callbacks.build(
                        "tview",
                        {
                            "action": "open",
                            "params": {"week": week_no, "student": s["student_id"]},
                        },
                        role=role,
                    ),
                )
            ]
        )
    rows.append(
        [
            types.InlineKeyboardButton(
                text="← К неделям",
                callback_data=callbacks.build(
                    "tview", {"action": "weeks", "params": {}}, role=role
                ),
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _back_to_students_markup(week_no: int, role: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="← К студентам",
                    callback_data=callbacks.build(
                        "tview",
                        {"action": "back", "params": {"week": week_no}},
                        role=role,
                    ),
                )
            ]
        ]
    )


@router.message(Command("week_submissions"))
async def tview_start(m: types.Message, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await m.answer("Недостаточно прав.")
    await m.answer("Выберите неделю:", reply_markup=_weeks_keyboard(actor.role, page=0))


@router.callback_query(_cb("tview", {"page"}))
async def tview_page(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    page = int(payload["params"].get("page", 0))
    try:
        await cq.message.edit_reply_markup(
            reply_markup=_weeks_keyboard(actor.role, page=page)
        )
    except Exception:
        await cq.message.answer(
            "Выберите неделю:", reply_markup=_weeks_keyboard(actor.role, page=page)
        )
    await cq.answer()


@router.callback_query(_cb("tview", {"weeks"}))
async def tview_weeks_root(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    callbacks.extract(cq.data, expected_role=actor.role)
    try:
        await cq.message.edit_text(
            "Выберите неделю:", reply_markup=_weeks_keyboard(actor.role, page=0)
        )
    except Exception:
        await cq.message.answer(
            "Выберите неделю:", reply_markup=_weeks_keyboard(actor.role, page=0)
        )
    await cq.answer()


@router.callback_query(_cb("tview", {"week"}))
async def tview_pick_week(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week_no = int(payload["params"].get("week", 0))
    students = list_students_with_submissions_by_week(week_no)
    if not students:
        await cq.message.answer(
            "Для этой недели сдач пока нет",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="← К неделям",
                            callback_data=callbacks.build(
                                "tview",
                                {"action": "weeks", "params": {}},
                                role=actor.role,
                            ),
                        )
                    ]
                ]
            ),
        )
        return await cq.answer()
    lines = [
        f"• {s.get('name') or s.get('tg_id')}: файлов {s['files_count']}"
        for s in students
    ]
    await cq.message.answer(
        "\n".join(lines[:100]),
        reply_markup=_students_keyboard(week_no, students, actor.role),
    )
    await cq.answer()


@router.callback_query(_cb("tview", {"open"}))
async def tview_open_student(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week_no = int(payload["params"].get("week", 0))
    student_id = payload["params"].get("student")
    files = list_week_submission_files_for_teacher(student_id, week_no)
    if not files:
        await cq.message.answer(
            "Файлов нет.", reply_markup=_back_to_students_markup(week_no, actor.role)
        )
        return await cq.answer()

    def _short_sha(x: str | None) -> str:
        return (x or "")[0:12]

    lines = [
        f"• #{f['id']} | {f.get('mime') or 'file'} | size={f['size_bytes']} | sha={_short_sha(f.get('sha256'))}"
        for f in files
    ]
    await cq.message.answer(
        "\n".join(lines[:100]),
        reply_markup=_back_to_students_markup(week_no, actor.role),
    )
    await cq.answer()


@router.callback_query(_cb("tview", {"back"}))
async def tview_back_to_students(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week_no = int(payload["params"].get("week", 0))
    students = list_students_with_submissions_by_week(week_no)
    if not students:
        await cq.message.answer(
            "Для этой недели сдач пока нет",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="← К неделям",
                            callback_data=callbacks.build(
                                "tview", {"action": "weeks", "params": {}}
                            ),
                        )
                    ]
                ]
            ),
        )
        return await cq.answer()
    lines = [
        f"• {s.get('name') or s.get('tg_id')}: файлов {s['files_count']}"
        for s in students
    ]
    await cq.message.answer(
        "\n".join(lines[:100]),
        reply_markup=_students_keyboard(week_no, students, actor.role),
    )
    await cq.answer()
