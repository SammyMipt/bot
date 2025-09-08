from __future__ import annotations

import os

from aiogram import F, Router, types
from aiogram.filters import Command

from app.core import callbacks, state_store
from app.core.auth import Identity
from app.core.repos_epic4 import get_active_material, list_weeks_with_titles
from app.db.conn import db

try:
    from aiogram.types import BufferedInputFile
except Exception:  # pragma: no cover
    BufferedInputFile = None  # type: ignore

router = Router(name="ui.teacher.stub")


def _uid(x: types.Message | types.CallbackQuery) -> int:
    return x.from_user.id


def _nav_key(uid: int) -> str:
    return f"t_nav:{uid}"


def _cw_key(uid: int) -> str:
    return f"t_cw:{uid}"


def cb(action: str, params: dict | None = None, role: str | None = None) -> str:
    payload = {"action": action}
    if params:
        payload.update(params)
    # op "t" — teacher UI namespace; role is dynamic (teacher/owner)
    return callbacks.build("t", payload, role=role)


def _is(op: str, actions: set[str]):
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


def _stack_get(uid: int) -> list[dict]:
    try:
        action, st = state_store.get(_nav_key(uid))
        if action != "t_nav":
            return []
        return st.get("stack") or []
    except Exception:
        return []


def _stack_set(uid: int, stack: list[dict]) -> None:
    state_store.put_at(_nav_key(uid), "t_nav", {"stack": stack}, ttl_sec=1800)


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


def _impersonation_active(uid: int) -> bool:
    try:
        action, st = state_store.get(f"impersonate:{uid}")
        return action == "imp_active" and st.get("exp", 0) >= state_store.now()
    except Exception:
        return False


def _main_menu_kb(role: str, uid: int) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = [
        [
            types.InlineKeyboardButton(
                text="➕ Создать расписание", callback_data=cb("sch_create", role=role)
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📅 Управление расписанием",
                callback_data=cb("sch_manage", role=role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="🧩 Мои пресеты", callback_data=cb("presets", role=role)
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📚 Методические материалы",
                callback_data=cb("materials", role=role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📝 Проверка работ", callback_data=cb("checkwork", role=role)
            )
        ],
    ]
    if _impersonation_active(uid):
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="👑 Меню владельца",
                    callback_data=callbacks.build(
                        "own", {"action": "start_owner"}, role="owner"
                    ),
                )
            ]
        )
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="↩️ Завершить имперсонизацию",
                    callback_data=callbacks.build(
                        "own", {"action": "imp_stop"}, role="owner"
                    ),
                )
            ]
        )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _week_id_by_no(week_no: int) -> int | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM weeks WHERE week_no=?", (week_no,)
        ).fetchone()
        return int(row[0]) if row else None


def _week_title(week_no: int) -> str:
    weeks = dict(list_weeks_with_titles(limit=200))
    return weeks.get(week_no, "")


def _materials_types_kb(week: int, role: str) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="📄 Материалы недели",
                callback_data=cb("materials_send", {"week": week, "t": "p"}, role=role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📘 Материалы для преподавателя",
                callback_data=cb("materials_send", {"week": week, "t": "m"}, role=role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📚 Конспект",
                callback_data=cb("materials_send", {"week": week, "t": "n"}, role=role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📊 Презентация",
                callback_data=cb("materials_send", {"week": week, "t": "s"}, role=role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="🎥 Запись лекции",
                callback_data=cb("materials_send", {"week": week, "t": "v"}, role=role),
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# ------- Entry points -------


@router.message(Command("teacher"))
async def teacher_menu_cmd(m: types.Message, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await m.answer("⛔ Доступ запрещён.")
    uid = _uid(m)
    _stack_reset(uid)
    await m.answer(
        "Главное меню преподавателя", reply_markup=_main_menu_kb(actor.role, uid)
    )


@router.message(Command("teacher_menu"))
async def teacher_menu_alt_cmd(m: types.Message, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await m.answer("⛔ Доступ запрещён.")
    uid = _uid(m)
    _stack_reset(uid)
    await m.answer(
        "Главное меню преподавателя", reply_markup=_main_menu_kb(actor.role, uid)
    )


@router.callback_query(_is("t", {"home"}))
async def tui_home(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    uid = _uid(cq)
    _stack_reset(uid)
    try:
        await cq.message.edit_text(
            "Главное меню преподавателя", reply_markup=_main_menu_kb(actor.role, uid)
        )
    except Exception:
        await cq.message.answer(
            "Главное меню преподавателя", reply_markup=_main_menu_kb(actor.role, uid)
        )
    await cq.answer()


@router.callback_query(_is("t", {"back"}))
async def tui_back(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    prev = _stack_pop(uid)
    if not prev:
        return await tui_home(cq, actor)
    screen = prev.get("s")
    params = prev.get("p") or {}
    if screen == "sch_create":
        return await tui_sch_create(cq, actor)
    if screen == "sch_manual":
        return await tui_sch_manual(cq, actor)
    if screen == "sch_manual_place":
        cq.data = cb(
            "sch_manual_place", {"mode": params.get("mode", "online")}, role=actor.role
        )
        return await tui_sch_manual_place(cq, actor)
    if screen == "sch_manual_date":
        cq.data = cb("sch_manual_date", role=actor.role)
        return await tui_sch_manual_date(cq, actor)
    if screen == "sch_manual_time":
        cq.data = cb("sch_manual_time", role=actor.role)
        return await tui_sch_manual_time(cq, actor)
    if screen == "sch_manual_duration":
        cq.data = cb("sch_manual_duration", role=actor.role)
        return await tui_sch_manual_duration(cq, actor)
    if screen == "sch_manual_capacity":
        cq.data = cb("sch_manual_capacity", role=actor.role)
        return await tui_sch_manual_capacity(cq, actor)
    if screen == "sch_manual_preview":
        cq.data = cb("sch_manual_preview", role=actor.role)
        return await tui_sch_manual_preview(cq, actor)
    if screen == "sch_preset":
        return await tui_sch_preset(cq, actor)
    if screen == "sch_preset_period":
        cq.data = cb("sch_preset_period", role=actor.role)
        return await tui_sch_preset_period(cq, actor)
    if screen == "sch_preset_preview":
        cq.data = cb("sch_preset_preview", role=actor.role)
        return await tui_sch_preset_preview(cq, actor)
    if screen == "sch_manage":
        return await tui_sch_manage(cq, actor)
    if screen == "presets":
        return await tui_presets(cq, actor)
    if screen == "presets_create":
        return await tui_presets_create(cq, actor)
    if screen == "materials":
        return await tui_materials(cq, actor)
    if screen == "materials_week":
        cq.data = cb("materials_week", {"week": params.get("week", 1)}, role=actor.role)
        return await tui_materials_week(cq, actor)
    if screen == "checkwork":
        return await tui_checkwork(cq, actor)
    if screen == "cw_by_date":
        return await tui_cw_by_date(cq, actor)
    if screen == "cw_by_student":
        return await tui_cw_by_student(cq, actor)
    return await tui_home(cq, actor)


# ------- Schedule: Create -------


def _sch_create_kb(role: str) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="🧱 Создать слоты (на день)",
                callback_data=cb("sch_manual", role=role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="⚡ Применить пресет", callback_data=cb("sch_preset", role=role)
            )
        ],
    ]
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"sch_create"}))
async def tui_sch_create(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    try:
        await cq.message.edit_text(
            "Создание расписания", reply_markup=_sch_create_kb(actor.role)
        )
    except Exception:
        await cq.message.answer(
            "Создание расписания", reply_markup=_sch_create_kb(actor.role)
        )
    await cq.answer()
    _stack_push(_uid(cq), "sch_create", {})


def _sch_manual_kb(role: str) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="🏫 Очно",
                callback_data=cb("sch_manual_place", {"mode": "offline"}, role=role),
            ),
            types.InlineKeyboardButton(
                text="🖥 Онлайн",
                callback_data=cb("sch_manual_place", {"mode": "online"}, role=role),
            ),
        ],
    ]
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"sch_manual"}))
async def tui_sch_manual(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Шаг 1/7 — формат слотов.\nВыберите формат приёма работ:"
    try:
        await cq.message.edit_text(text, reply_markup=_sch_manual_kb(actor.role))
    except Exception:
        await cq.message.answer(text, reply_markup=_sch_manual_kb(actor.role))
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual", {})


def _sch_manual_date_kb(role: str) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="−2",
                callback_data=cb("sch_manual_time", {"date": "m2"}, role=role),
            ),
            types.InlineKeyboardButton(
                text="−1",
                callback_data=cb("sch_manual_time", {"date": "m1"}, role=role),
            ),
            types.InlineKeyboardButton(
                text="Сегодня",
                callback_data=cb("sch_manual_time", {"date": "today"}, role=role),
            ),
            types.InlineKeyboardButton(
                text="+1",
                callback_data=cb("sch_manual_time", {"date": "p1"}, role=role),
            ),
            types.InlineKeyboardButton(
                text="+2",
                callback_data=cb("sch_manual_time", {"date": "p2"}, role=role),
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="📅 Будущее",
                callback_data=cb("sch_manual_time", {"date": "future"}, role=role),
            ),
        ],
    ]
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"sch_manual_date"}))
async def tui_sch_manual_date(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    # consume
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Шаг 3/7 — дата. Выберите дату для создания слотов:"
    try:
        await cq.message.edit_text(text, reply_markup=_sch_manual_date_kb(actor.role))
    except Exception:
        await cq.message.answer(text, reply_markup=_sch_manual_date_kb(actor.role))
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_date", {})


@router.callback_query(_is("t", {"sch_manual_time"}))
async def tui_sch_manual_time(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    # consume
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = (
        "Шаг 4/7 — время. Выбор времени пока не реализован.\n"
        "Подсказка Dual TZ: время отображается в TZ курса; при отличии пользовательской TZ показывается доп. подсказка.\n"
        "Нажмите «Далее», чтобы продолжить."
    )
    rows = [
        [
            types.InlineKeyboardButton(
                text="Далее", callback_data=cb("sch_manual_duration", role=actor.role)
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_time", {})


@router.callback_query(_is("t", {"sch_manual_duration"}))
async def tui_sch_manual_duration(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Шаг 5/7 — длительность. Выберите длительность слотов:"
    rows = [
        [
            types.InlineKeyboardButton(
                text="15",
                callback_data=cb("sch_manual_capacity", {"dur": 15}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="20",
                callback_data=cb("sch_manual_capacity", {"dur": 20}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="25",
                callback_data=cb("sch_manual_capacity", {"dur": 25}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="30",
                callback_data=cb("sch_manual_capacity", {"dur": 30}, role=actor.role),
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="Больше…",
                callback_data=cb("sch_manual_capacity", {"dur": 45}, role=actor.role),
            ),
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_duration", {})


@router.callback_query(_is("t", {"sch_manual_capacity"}))
async def tui_sch_manual_capacity(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Шаг 6/7 — вместимость. Выберите вместимость слотов:"
    rows = [
        [
            types.InlineKeyboardButton(
                text="1",
                callback_data=cb("sch_manual_preview", {"cap": 1}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="2",
                callback_data=cb("sch_manual_preview", {"cap": 2}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="3",
                callback_data=cb("sch_manual_preview", {"cap": 3}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="10",
                callback_data=cb("sch_manual_preview", {"cap": 10}, role=actor.role),
            ),
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_capacity", {})


@router.callback_query(_is("t", {"sch_manual_preview"}))
async def tui_sch_manual_preview(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Шаг 7/7 — предпросмотр.\n" "Заглушка: создание слотов пока не реализовано."
    rows = [
        [
            types.InlineKeyboardButton(
                text="👁 Показать список", callback_data=cb("stub", role=actor.role)
            ),
            types.InlineKeyboardButton(
                text="✅ Создать", callback_data=cb("stub", role=actor.role)
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="👁 Показать список дат", callback_data=cb("stub", role=actor.role)
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_preview", {})


# ------- Schedule: Apply preset -------


def _sch_preset_kb(role: str, page: int = 0) -> types.InlineKeyboardMarkup:
    # Placeholder: 5 mock presets (no DB ops here)
    presets = [f"Пресет {i + 1}" for i in range(5)]
    rows: list[list[types.InlineKeyboardButton]] = []
    for p in presets:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=p, callback_data=cb("sch_preset_period", {"pid": p}, role=role)
                )
            ]
        )
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"sch_preset"}))
async def tui_sch_preset(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Выберите пресет (демо-список)"
    kb = _sch_preset_kb(actor.role)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "sch_preset", {})


@router.callback_query(_is("t", {"sch_preset_period"}))
async def tui_sch_preset_period(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    rows = [
        [
            types.InlineKeyboardButton(
                text="4 недели",
                callback_data=cb("sch_preset_preview", {"per": 4}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="8 недель",
                callback_data=cb("sch_preset_preview", {"per": 8}, role=actor.role),
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="Вручную",
                callback_data=cb("sch_preset_preview", {"per": 0}, role=actor.role),
            ),
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    text = "Период применения пресета:"
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "sch_preset_period", {})


@router.callback_query(_is("t", {"sch_preset_preview"}))
async def tui_sch_preset_preview(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Предпросмотр пресета. Заглушка: применение пресета не реализовано."
    rows = [
        [
            types.InlineKeyboardButton(
                text="✅ Создать", callback_data=cb("stub", role=actor.role)
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "sch_preset_preview", {})


# ------- Schedule: Manage -------


def _sch_manage_kb(role: str) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(text="−2", callback_data=cb("stub", role=role)),
            types.InlineKeyboardButton(text="−1", callback_data=cb("stub", role=role)),
            types.InlineKeyboardButton(
                text="Сегодня", callback_data=cb("stub", role=role)
            ),
            types.InlineKeyboardButton(text="+1", callback_data=cb("stub", role=role)),
            types.InlineKeyboardButton(text="+2", callback_data=cb("stub", role=role)),
        ],
        [
            types.InlineKeyboardButton(
                text="🗓 Все слоты", callback_data=cb("stub", role=role)
            ),
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"sch_manage"}))
async def tui_sch_manage(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Управление расписанием. Заглушка: список слотов не реализован."
    try:
        await cq.message.edit_text(text, reply_markup=_sch_manage_kb(actor.role))
    except Exception:
        await cq.message.answer(text, reply_markup=_sch_manage_kb(actor.role))
    await cq.answer()
    _stack_push(_uid(cq), "sch_manage", {})


# ------- Presets -------


def _presets_root_kb(role: str) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="➕ Создать пресет", callback_data=cb("presets_create", role=role)
            )
        ],
        [
            types.InlineKeyboardButton(
                text="👁 Просмотр пресетов",
                callback_data=cb("presets_view", {"p": 0}, role=role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="🗑 Удалить пресет", callback_data=cb("presets_delete", role=role)
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"presets"}))
async def tui_presets(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Мои пресеты. Заглушка: список пресетов не реализован."
    try:
        await cq.message.edit_text(text, reply_markup=_presets_root_kb(actor.role))
    except Exception:
        await cq.message.answer(text, reply_markup=_presets_root_kb(actor.role))
    await cq.answer()
    _stack_push(_uid(cq), "presets", {})


def _presets_create_kb(step: int, role: str) -> types.InlineKeyboardMarkup:
    # generic Next + nav
    rows = [
        [
            types.InlineKeyboardButton(
                text="Далее",
                callback_data=cb("presets_create_next", {"step": step}, role=role),
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"presets_create"}))
async def tui_presets_create(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Создание пресета — шаг 1/7 (название). Ввод текста пока не реализован."
    kb = _presets_create_kb(step=1, role=actor.role)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "presets_create", {})


@router.callback_query(_is("t", {"presets_create_next"}))
async def tui_presets_create_next(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    step = int(payload.get("step", 1)) + 1
    if step <= 6:
        text = f"Создание пресета — шаг {step}/7. Заглушка."
        kb = _presets_create_kb(step=step, role=actor.role)
    else:
        text = "Создание пресета — предпросмотр. Заглушка: сохранение не реализовано."
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="✅ Сохранить", callback_data=cb("stub", role=actor.role)
                    )
                ],
                _nav_keyboard().inline_keyboard[0],
            ]
        )
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


# ------- Materials -------


def _weeks_keyboard(role: str, page: int = 0) -> types.InlineKeyboardMarkup:
    weeks = list_weeks_with_titles(limit=200)
    per_page = 8
    total_pages = max(1, (len(weeks) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = weeks[start : start + per_page]

    rows: list[list[types.InlineKeyboardButton]] = []
    for n, title in chunk:
        label = f"📘 Неделя {n}"
        if title:
            label += f". {title}"
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=label,
                    callback_data=cb("materials_week", {"week": n}, role=role),
                )
            ]
        )

    if total_pages > 1:
        nav: list[types.InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                types.InlineKeyboardButton(
                    text="« Назад",
                    callback_data=cb("materials_page", {"page": page - 1}, role=role),
                )
            )
        if page < total_pages - 1:
            nav.append(
                types.InlineKeyboardButton(
                    text="Вперёд »",
                    callback_data=cb("materials_page", {"page": page + 1}, role=role),
                )
            )
        if nav:
            rows.append(nav)
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"materials"}))
async def tui_materials(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        await cq.answer("⛔ Сессия истекла. Начните заново.", show_alert=True)
        return await tui_home(cq, actor)
    text = "📚 <b>Методические материалы</b>\nВыберите неделю:"
    kb = _weeks_keyboard(actor.role, page=0)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "materials", {})


@router.callback_query(_is("t", {"materials_page"}))
async def tui_materials_page(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    page = int(payload.get("page", 0))
    try:
        await cq.message.edit_reply_markup(
            reply_markup=_weeks_keyboard(actor.role, page=page)
        )
    except Exception:
        await cq.message.answer(
            "📚 <b>Методические материалы</b>\nВыберите неделю:",
            reply_markup=_weeks_keyboard(actor.role, page=page),
            parse_mode="HTML",
        )
    await cq.answer()


@router.callback_query(_is("t", {"materials_week"}))
async def tui_materials_week(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week_no = int(payload.get("week", 0))
    title = _week_title(week_no)
    if title:
        text = f"📚 <b>Неделя {week_no}. {title}</b>\nВыберите материал:"
    else:
        text = f"📚 <b>Неделя {week_no}</b>\nВыберите материал:"
    kb = _materials_types_kb(week_no, actor.role)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "materials_week", {"week": week_no})


@router.callback_query(_is("t", {"materials_send"}))
async def tui_materials_send(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week_no = int(payload.get("week", 0))
    t = payload.get("t", "p")
    wk_id = _week_id_by_no(week_no)
    if wk_id is None:
        return await cq.answer("Неделя не найдена", show_alert=True)
    mat = get_active_material(wk_id, t)
    if not mat:
        return await cq.answer("Нет активной версии", show_alert=True)
    title = _week_title(week_no)
    labels = {
        "p": ("📄", "Материалы недели"),
        "m": ("📘", "Материалы для преподавателя"),
        "n": ("📚", "Конспект"),
        "s": ("📊", "Презентация"),
        "v": ("🎥", "Запись лекции"),
    }
    emoji, name = labels.get(t, ("📄", "Материал"))
    if t == "v":
        try:
            msg = f"{emoji} <b>Неделя {week_no}"
            if title:
                msg += f". {title}"
            msg += f'.</b> <a href="{mat.path}">{name}</a>'
            await cq.message.answer(
                msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            return await cq.answer("Не удалось отправить ссылку", show_alert=True)
        await cq.answer("✅ Ссылка отправлена")
        return
    if not BufferedInputFile:
        return await cq.answer("Нет активной версии", show_alert=True)
    try:
        with open(mat.path, "rb") as f:
            data = f.read()
        fname = os.path.basename(mat.path) or f"week{week_no}_{t}.bin"
        caption = f"{emoji} <b>Неделя {week_no}"
        if title:
            caption += f". {title}"
        caption += f".</b> {name}."
        await cq.message.answer_document(
            BufferedInputFile(data, filename=fname),
            caption=caption,
            parse_mode="HTML",
        )
    except Exception:
        return await cq.answer("Не удалось подготовить файл", show_alert=True)
    await cq.answer("✅ Файл отправлен")


# ------- Check work -------


def _checkwork_kb(role: str) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="📅 По дате/слоту", callback_data=cb("cw_by_date", role=role)
            ),
            types.InlineKeyboardButton(
                text="🔎 По студенту", callback_data=cb("cw_by_student", role=role)
            ),
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"checkwork"}))
async def tui_checkwork(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Проверка работ: выберите режим."
    try:
        await cq.message.edit_text(text, reply_markup=_checkwork_kb(actor.role))
    except Exception:
        await cq.message.answer(text, reply_markup=_checkwork_kb(actor.role))
    await cq.answer()
    _stack_push(_uid(cq), "checkwork", {})


@router.callback_query(_is("t", {"cw_by_date"}))
async def tui_cw_by_date(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "По дате/слоту. Заглушка: список дат/слотов не реализован."
    rows = [
        [
            types.InlineKeyboardButton(
                text="Сегодня", callback_data=cb("stub", role=actor.role)
            ),
            types.InlineKeyboardButton(
                text="+1", callback_data=cb("stub", role=actor.role)
            ),
            types.InlineKeyboardButton(
                text="🗓 Все даты", callback_data=cb("stub", role=actor.role)
            ),
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "cw_by_date", {})


@router.callback_query(_is("t", {"cw_by_student"}))
async def tui_cw_by_student(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    uid = _uid(cq)
    state_store.put_at(_cw_key(uid), "t_cw", {"mode": "await_surname"}, ttl_sec=600)
    text = (
        "По студенту. Введите фамилию текстом.\n"
        "Заглушка: поиск и результаты — не реализовано."
    )
    try:
        await cq.message.edit_text(text, reply_markup=_nav_keyboard())
    except Exception:
        await cq.message.answer(text, reply_markup=_nav_keyboard())
    await cq.answer()
    _stack_push(uid, "cw_by_student", {})


def _awaits_cw_surname(m: types.Message) -> bool:
    try:
        action, st = state_store.get(_cw_key(m.from_user.id))
    except Exception:
        return False
    return action == "t_cw" and st.get("mode") == "await_surname"


@router.message(F.text, _awaits_cw_surname)
async def tui_cw_receive_surname(m: types.Message, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return
    # Consume awaiting state
    try:
        state_store.delete(_cw_key(_uid(m)))
    except Exception:
        pass
    # Show placeholder result item to proceed through flow
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Открыть демо-студента",
                    callback_data=cb("stub", role=actor.role),
                )
            ],
            _nav_keyboard().inline_keyboard[0],
        ]
    )
    await m.answer("Заглушка: результаты поиска не реализованы.", reply_markup=kb)


# ------- Generic stub handler -------


@router.callback_query(_is("t", {"stub"}))
async def tui_stub_action(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    # consume
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    await cq.answer("⛔ Функция не реализована", show_alert=True)


@router.callback_query(_is("t", {"sch_manual_place"}))
async def tui_sch_manual_place(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    mode = (payload.get("mode") or "online").strip()
    try:
        state_store.put_at(
            f"t_manual_ctx:{_uid(cq)}", "t_manual", {"mode": mode}, ttl_sec=900
        )
    except Exception:
        pass
    is_online = mode == "online"
    if is_online:
        text = (
            "Шаг 2/7 — место проведения (онлайн).\n"
            "Заглушка: ввод ссылки пока не реализован."
        )
    else:
        text = (
            "Шаг 2/7 — место проведения (очно).\n"
            "Заглушка: ввод аудитории пока не реализован."
        )
    rows = [
        [
            types.InlineKeyboardButton(
                text="Далее",
                callback_data=cb("sch_manual_date", {"mode": mode}, role=actor.role),
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_place", {"mode": mode})
