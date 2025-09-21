from __future__ import annotations

import os

from aiogram import F, Router, types
from aiogram.filters import Command

from app.core import audit, callbacks, state_store
from app.core.auth import Identity
from app.core.repos_epic4 import get_active_material, list_weeks_with_titles
from app.core.slots_repo import create_slots_for_range, generate_timeslots
from app.db.conn import db

try:
    from aiogram.types import BufferedInputFile
except Exception:  # pragma: no cover
    BufferedInputFile = None  # type: ignore

router = Router(name="ui.teacher.stub")


def _uid(x: types.Message | types.CallbackQuery) -> int:
    return x.from_user.id


# ------- Error mapping (DomainError-like) -------

# Basic UI mapping aligned with L2/L3 docs
ERROR_MESSAGES: dict[str, str] = {
    "E_INPUT_INVALID": "⛔ Некорректный ввод",
    "E_DURATION_EXCEEDED": "⚠️ Превышен лимит 6 часов",
    "E_CAP_EXCEEDED": "⚠️ Превышена вместимость",
    "E_ALREADY_EXISTS": "⚠️ Дубликат/конфликт",
    "E_ACCESS_DENIED": "⛔ Нет прав для действия",
    "E_STATE_INVALID": "⛔ Некорректное состояние",
}


async def _toast_error(
    cq: types.CallbackQuery, code: str, default_message: str | None = None
) -> None:
    msg = ERROR_MESSAGES.get(code, default_message or "⛔ Произошла ошибка")
    await cq.answer(msg, show_alert=True)


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
    state_store.put_at(_nav_key(uid), "t_nav", {"stack": stack}, ttl_sec=900)


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


def _stack_last_params(uid: int, screen: str) -> dict | None:
    try:
        st = _stack_get(uid)
        for item in reversed(st):
            if item.get("s") == screen:
                return item.get("p") or {}
    except Exception:
        pass
    return None


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
        [
            types.InlineKeyboardButton(
                text="⚙️ Настройки", callback_data=cb("settings", role=role)
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


# ------- Settings: Teacher TZ -------


def _teacher_tz(actor: Identity) -> str:
    from app.services.common.time_service import get_course_tz

    try:
        with db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "tz" not in cols:
                return get_course_tz()
            row = conn.execute(
                "SELECT tz FROM users WHERE id=?", (actor.id,)
            ).fetchone()
            if row and row[0]:
                return str(row[0])
    except Exception:
        pass
    return get_course_tz()


def _teacher_settings_kb(actor: Identity) -> types.InlineKeyboardMarkup:
    tz = _teacher_tz(actor)
    rows = [
        [
            types.InlineKeyboardButton(
                text=f"Часовой пояс: {tz}", callback_data=cb("tz", role=actor.role)
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"settings"}))
async def tui_settings(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Настройки преподавателя"
    kb = _teacher_settings_kb(actor)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


# Simple TZ picker for teacher (reuse curated list and grouping like owner)
def _tz_catalog() -> list[str]:
    try:
        from app.bot.ui_owner_stub import (
            _tz_catalog as owner_tz_catalog,  # type: ignore
        )

        return owner_tz_catalog()
    except Exception:
        # Minimal fallback
        return [
            "UTC",
            "Europe/Moscow",
            "Europe/Kiev",
            "Europe/Berlin",
            "Europe/London",
            "America/New_York",
            "America/Los_Angeles",
            "Asia/Tokyo",
            "Asia/Shanghai",
            "Asia/Kolkata",
        ]


def _tz_grouping() -> tuple[list[str], dict[str, list[int]]]:
    zones = _tz_catalog()
    regions: dict[str, list[int]] = {}
    for idx, name in enumerate(zones):
        region = name.split("/", 1)[0] if "/" in name else name
        regions.setdefault(region, []).append(idx)
    region_names = sorted(regions.keys())
    return region_names, regions


def _tz_offset_str(tzname: str) -> str:
    from datetime import datetime, timezone

    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(timezone.utc)
        off = now.astimezone(ZoneInfo(tzname)).utcoffset()
        if off is None:
            return "+00:00"
        total = int(off.total_seconds())
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        hh = total // 3600
        mm = (total % 3600) // 60
        return f"{sign}{hh:02d}:{mm:02d}"
    except Exception:
        return "+00:00"


def _tz_regions_kb(
    page: int = 0, per_page: int = 12, *, role: str | None = None
) -> types.InlineKeyboardMarkup:
    regions, _ = _tz_grouping()
    total_pages = max(1, (len(regions) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = regions[start : start + per_page]
    rows: list[list[types.InlineKeyboardButton]] = []
    for idx, r in enumerate(chunk, start=start):
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=r, callback_data=cb("tz_reg_set", {"r": idx}, role=role)
                )
            ]
        )
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb("tz_reg_page", {"p": page - 1}, role=role),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb("tz_reg_page", {"p": page + 1}, role=role),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _tz_cities_kb(
    region_idx: int, page: int = 0, per_page: int = 12, *, role: str | None = None
) -> types.InlineKeyboardMarkup:
    regions, mapping = _tz_grouping()
    zones = _tz_catalog()
    if region_idx < 0 or region_idx >= len(regions):
        return _tz_regions_kb(0, role=role)
    region = regions[region_idx]
    global_indices = mapping.get(region, [])
    items: list[tuple[int, str]] = []
    for gi in global_indices:
        name = zones[gi]
        city = name.split("/", 1)[1] if "/" in name else name
        off = _tz_offset_str(name)
        items.append((gi, f"{city} (UTC{off})"))
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = items[start : start + per_page]
    rows: list[list[types.InlineKeyboardButton]] = []
    for gi, label in chunk:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=label, callback_data=cb("tz_set", {"i": gi}, role=role)
                )
            ]
        )
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb(
                    "tz_city_page", {"r": region_idx, "p": page - 1}, role=role
                ),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb(
                    "tz_city_page", {"r": region_idx, "p": page + 1}, role=role
                ),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [types.InlineKeyboardButton(text="⬅️ Регион", callback_data=cb("tz", role=role))]
    )
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"tz"}))
async def tui_tz(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    text = "Выберите регион"
    try:
        await cq.message.edit_text(
            text, reply_markup=_tz_regions_kb(0, role=actor.role)
        )
    except Exception:
        await cq.message.answer(text, reply_markup=_tz_regions_kb(0, role=actor.role))
    await cq.answer()


@router.callback_query(_is("t", {"tz_reg_page"}))
async def tui_tz_reg_page(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    p = int(payload.get("p", 0))
    try:
        await cq.message.edit_reply_markup(
            reply_markup=_tz_regions_kb(p, role=actor.role)
        )
    except Exception:
        await cq.message.answer(
            "Выбор региона", reply_markup=_tz_regions_kb(p, role=actor.role)
        )
    await cq.answer()


@router.callback_query(_is("t", {"tz_reg_set"}))
async def tui_tz_reg_set(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    r = int(payload.get("r", 0))
    try:
        await cq.message.edit_text(
            "Выберите город/зону", reply_markup=_tz_cities_kb(r, 0, role=actor.role)
        )
    except Exception:
        await cq.message.answer(
            "Выберите город/зону", reply_markup=_tz_cities_kb(r, 0, role=actor.role)
        )
    await cq.answer()


@router.callback_query(_is("t", {"tz_city_page"}))
async def tui_tz_city_page(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    r = int(payload.get("r", 0))
    p = int(payload.get("p", 0))
    try:
        await cq.message.edit_reply_markup(
            reply_markup=_tz_cities_kb(r, p, role=actor.role)
        )
    except Exception:
        await cq.message.answer(
            "Выбор зоны", reply_markup=_tz_cities_kb(r, p, role=actor.role)
        )
    await cq.answer()


@router.callback_query(_is("t", {"tz_set"}))
async def tui_tz_set(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    i = int(payload.get("i", -1))
    zones = _tz_catalog()
    if i < 0 or i >= len(zones):
        return await cq.answer("Некорректный выбор", show_alert=True)
    tzname = zones[i]
    # Persist only if schema supports it
    saved = False
    try:
        with db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "tz" in cols:
                conn.execute(
                    "UPDATE users SET tz=?, updated_at_utc=strftime('%s','now') WHERE id=?",
                    (tzname, actor.id),
                )
                conn.commit()
                saved = True
    except Exception:
        saved = False
    msg = (
        f"✅ Часовой пояс сохранён: {tzname}"
        if saved
        else "⚠️ Невозможно сохранить TZ: нет поддержки в БД"
    )
    kb = _teacher_settings_kb(actor)
    try:
        await cq.message.edit_text(msg, reply_markup=kb)
    except Exception:
        await cq.message.answer(msg, reply_markup=kb)
    await cq.answer()


# ------- Helpers: manual schedule context -------


def _manual_ctx_key(uid: int) -> str:
    return f"t_manual_ctx:{uid}"


def _manual_ctx_get(uid: int) -> dict:
    try:
        action, st = state_store.get(_manual_ctx_key(uid))
        if action != "t_manual":
            return {}
        return dict(st)
    except Exception:
        return {}


def _manual_ctx_put(uid: int, patch: dict) -> None:
    cur = _manual_ctx_get(uid)
    cur.update(patch)
    state_store.put_at(_manual_ctx_key(uid), "t_manual", cur, ttl_sec=900)


def _date_from_choice(choice: str) -> tuple[int, int, int]:
    """Return (Y, M, D) in course TZ for a choice like 'today','m1','p2'."""
    import datetime as _dt

    from app.services.common.time_service import course_today

    today = course_today().date()
    delta = 0
    if choice == "m2":
        delta = -2
    elif choice == "m1":
        delta = -1
    elif choice == "today":
        delta = 0
    elif choice == "p1":
        delta = 1
    elif choice == "p2":
        delta = 2
    else:
        delta = 3  # future stub
    dt = today + _dt.timedelta(days=delta)
    return dt.year, dt.month, dt.day


def _utc_ts(year: int, month: int, day: int, hour: int, minute: int) -> int:
    # Interpret local inputs in course TZ and convert to UTC
    from app.services.common.time_service import local_to_utc_ts

    return local_to_utc_ts(year, month, day, hour, minute)


def _loc_key(uid: int) -> str:
    return f"t_loc:{uid}"


def _awaits_manual_loc(m: types.Message) -> bool:
    try:
        action, st = state_store.get(_loc_key(m.from_user.id))
        return action == "t_loc" and st.get("mode") in ("online", "offline")
    except Exception:
        return False


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
    if screen == "sch_create":
        return await tui_sch_create(cq, actor)
    if screen == "sch_manual":
        return await tui_sch_manual(cq, actor)
    if screen == "sch_manual_place":
        return await tui_sch_manual_place(cq, actor)
    if screen == "sch_manual_date":
        return await tui_sch_manual_date(cq, actor)
    if screen == "sch_manual_time":
        return await tui_sch_manual_time(cq, actor)
    if screen == "sch_manual_duration":
        return await tui_sch_manual_duration(cq, actor)
    if screen == "sch_manual_duration_more":
        return await tui_sch_manual_duration(cq, actor)
    if screen == "sch_manual_capacity":
        return await tui_sch_manual_capacity(cq, actor)
    if screen == "sch_manual_capacity_more":
        return await tui_sch_manual_capacity(cq, actor)
    if screen == "sch_manual_preview":
        return await tui_sch_manual_preview(cq, actor)
    if screen == "sch_preset":
        return await tui_sch_preset(cq, actor)
    if screen == "sch_preset_period":
        return await tui_sch_preset_period(cq, actor)
    if screen == "sch_preset_preview":
        return await tui_sch_preset_preview(cq, actor)
    if screen == "sch_manage":
        return await tui_sch_manage(cq, actor)
    if screen == "sch_days":
        return await tui_sch_manage(cq, actor)
    if screen == "sch_day":
        # Reopen the days list if user goes back from a day view
        return await tui_sch_manage(cq, actor)
    if screen == "sch_manage_day":
        return await tui_sch_manage_day(cq, actor)
    if screen == "sch_manage_all":
        return await tui_sch_manage_all(cq, actor)
    if screen == "sch_slot":
        # Go back to the last list (day/all) if present
        last_day = _stack_last_params(_uid(cq), "sch_manage_day") or _stack_last_params(
            _uid(cq), "sch_day"
        )
        if last_day:
            # If we have a concrete picked day, return to that day view
            if set(last_day.keys()) >= {"y", "m", "d"}:
                cq.data = cb(
                    "sch_day",
                    {"y": last_day["y"], "m": last_day["m"], "d": last_day["d"]},
                    role=actor.role,
                )
                return await tui_sch_day(cq, actor)
            return await tui_sch_manage_day(cq, actor)
        last_all = _stack_last_params(_uid(cq), "sch_manage_all")
        if last_all:
            return await tui_sch_manage_all(cq, actor)
        return await tui_sch_manage(cq, actor)
    if screen == "sch_slot_students":
        return await tui_sch_slot(cq, actor)
    if screen == "sch_slot_student":
        return await tui_sch_slot_students(cq, actor)
    if screen == "presets":
        return await tui_presets(cq, actor)
    if screen == "presets_create":
        return await tui_presets_create(cq, actor)
    if screen == "materials":
        return await tui_materials(cq, actor)
    if screen == "materials_week":
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


def _last_deadline_ts() -> int | None:
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT MAX(deadline_ts_utc) FROM weeks WHERE deadline_ts_utc IS NOT NULL"
            ).fetchone()
            if row and row[0] is not None:
                return int(row[0])
    except Exception:
        return None
    return None


def _ru_wd(d: int) -> str:
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return names[d % 7]


def _date_page_kb(role: str, page: int = 0) -> types.InlineKeyboardMarkup:
    import datetime as _dt

    from app.services.common.time_service import course_today, to_course_dt

    today = course_today().date()
    last_deadline = _last_deadline_ts()
    max_days = 30
    if last_deadline:
        dd = to_course_dt(last_deadline).date()
        max_days = max(1, min(max_days, (dd - today).days + 1))
    days = [today + _dt.timedelta(days=i) for i in range(max_days)]

    per_page = 7
    total_pages = max(1, (len(days) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = days[start : start + per_page]

    rows: list[list[types.InlineKeyboardButton]] = []
    for d in chunk:
        label = f"{_ru_wd(d.weekday())} {d.day:02d}.{d.month:02d}"
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=label,
                    callback_data=cb(
                        "sch_manual_date_pick",
                        {"y": d.year, "m": d.month, "d": d.day},
                        role=role,
                    ),
                )
            ]
        )

    if total_pages > 1:
        nav: list[types.InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                types.InlineKeyboardButton(
                    text="« Назад",
                    callback_data=cb(
                        "sch_manual_date_page", {"p": page - 1}, role=role
                    ),
                )
            )
        if page < total_pages - 1:
            nav.append(
                types.InlineKeyboardButton(
                    text="Вперёд »",
                    callback_data=cb(
                        "sch_manual_date_page", {"p": page + 1}, role=role
                    ),
                )
            )
        if nav:
            rows.append(nav)
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
    ctx = _manual_ctx_get(_uid(cq))
    mode = (ctx.get("mode") or "online").strip()
    loc = (ctx.get("location") or "").strip()
    if mode == "online" and not loc:
        await cq.answer("⛔ Сначала отправьте ссылку", show_alert=True)
        return await tui_sch_manual_place(cq, actor)
    if int(ctx.get("loc_saved", 0)) == 1:
        try:
            await cq.answer(
                "✅ "
                + ("Ссылка сохранена" if mode == "online" else "Аудитория сохранена")
            )
        except Exception:
            pass
        _manual_ctx_put(_uid(cq), {"loc_saved": 0})
    text = "Шаг 3/7 — дата. Выберите дату для создания слотов:"
    try:
        await cq.message.edit_text(text, reply_markup=_date_page_kb(actor.role, 0))
    except Exception:
        await cq.message.answer(text, reply_markup=_date_page_kb(actor.role, 0))
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_date", {})


@router.callback_query(_is("t", {"sch_manual_date_page"}))
async def tui_sch_manual_date_page(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    page = int(payload.get("p", 0))
    try:
        await cq.message.edit_reply_markup(reply_markup=_date_page_kb(actor.role, page))
    except Exception:
        await cq.message.answer(
            "Шаг 3/7 — дата. Выберите дату для создания слотов:",
            reply_markup=_date_page_kb(actor.role, page),
        )
    await cq.answer()


@router.callback_query(_is("t", {"sch_manual_date_pick"}))
async def tui_sch_manual_date_pick(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    y = int(payload.get("y", 0))
    m = int(payload.get("m", 0))
    d = int(payload.get("d", 0))
    _manual_ctx_put(_uid(cq), {"y": y, "m": m, "d": d})
    return await tui_sch_manual_time(cq, actor)


@router.callback_query(_is("t", {"sch_manual_time"}))
async def tui_sch_manual_time(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    # consume (no additional params expected)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    # New flow: choose part of day
    text = "Шаг 4/7 — время. Выберите часть дня:"
    parts = [
        ("morning", "🌅 Утро (08–12)"),
        ("day", "🌞 День (12–16)"),
        ("evening", "🌇 Вечер (16–20)"),
        ("late", "🌙 Поздний вечер (20–24)"),
    ]
    rows: list[list[types.InlineKeyboardButton]] = [
        [
            types.InlineKeyboardButton(
                text=label,
                callback_data=cb(
                    "sch_manual_time_start", {"part": code, "p": 0}, role=actor.role
                ),
            )
        ]
        for code, label in parts
    ]
    rows.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_time", {})


def _part_range(part: str) -> tuple[int, int, int, int]:
    # Morning: 08:00–11:50; Day: 12:00–15:50; Evening: 16:00–19:50; Late: 20:00–23:50
    if part == "morning":
        return 8, 0, 11, 50
    if part == "day":
        return 12, 0, 15, 50
    if part == "evening":
        return 16, 0, 19, 50
    return 20, 0, 23, 50


def _times_between(
    h1: int, m1: int, h2: int, m2: int, step: int = 10
) -> list[tuple[int, int]]:
    items: list[tuple[int, int]] = []
    cur = h1 * 60 + m1
    end = h2 * 60 + m2
    while cur <= end:
        items.append((cur // 60, cur % 60))
        cur += step
    return items


@router.callback_query(_is("t", {"sch_manual_time_start"}))
async def tui_sch_manual_time_start(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    part = (payload.get("part") or "morning").strip()
    page = int(payload.get("p", 0))
    h1, m1, h2, m2 = _part_range(part)
    all_times = _times_between(h1, m1, h2, m2, step=10)
    per_page = 12
    total_pages = max(1, (len(all_times) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = all_times[start : start + per_page]
    text = "Шаг 4/7 — время. Выберите время начала:"
    rows: list[list[types.InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 4):
        row = [
            types.InlineKeyboardButton(
                text=f"{hh:02d}:{mm:02d}",
                callback_data=cb(
                    "sch_manual_time_start_pick",
                    {"h": hh, "m": mm, "part": part, "p": page},
                    role=actor.role,
                ),
            )
            for hh, mm in chunk[i : i + 4]
        ]
        rows.append(row)
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb(
                    "sch_manual_time_start",
                    {"part": part, "p": page - 1},
                    role=actor.role,
                ),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb(
                    "sch_manual_time_start",
                    {"part": part, "p": page + 1},
                    role=actor.role,
                ),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()


@router.callback_query(_is("t", {"sch_manual_time_start_min"}))
async def tui_sch_manual_time_start_min(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    # Deprecated handler: redirect to new flow if invoked
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    h = int(payload.get("h", 9)) if "h" in payload else 9
    m = int(payload.get("m", 0)) if "m" in payload else 0
    _manual_ctx_put(_uid(cq), {"sh": h, "sm": m})
    return await tui_sch_manual_time_end(cq, actor)


@router.callback_query(_is("t", {"sch_manual_time_start_pick"}))
async def tui_sch_manual_time_start_pick(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    h = int(payload.get("h", 9))
    m = int(payload.get("m", 0))
    _manual_ctx_put(_uid(cq), {"sh": h, "sm": m})
    return await tui_sch_manual_time_end(cq, actor)


@router.callback_query(_is("t", {"sch_manual_time_end"}))
async def tui_sch_manual_time_end(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    page = int(payload.get("p", 0))
    ctx = _manual_ctx_get(_uid(cq))
    sh, sm = int(ctx.get("sh", 9)), int(ctx.get("sm", 0))
    # End times: from start+10min to min(23:50, start+6h), align to 10-min grid
    start_min = sh * 60 + sm + 10
    if start_min % 10 != 0:
        start_min += 10 - (start_min % 10)
    eh, em = 23, 50
    # Build times list
    all_times = []
    cur = start_min
    end_total = eh * 60 + em
    # apply 6h cap relative to chosen start
    cap_total = sh * 60 + sm + 360
    if cap_total < end_total:
        end_total = cap_total
    while cur <= end_total:
        all_times.append((cur // 60, cur % 60))
        cur += 10
    per_page = 12
    total_pages = max(1, (len(all_times) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    begin = page * per_page
    chunk = all_times[begin : begin + per_page]
    text = "Шаг 4/7 — время. Выберите время окончания:"
    rows: list[list[types.InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 4):
        row = [
            types.InlineKeyboardButton(
                text=f"{hh:02d}:{mm:02d}",
                callback_data=cb(
                    "sch_manual_time_end_pick",
                    {"h": hh, "m": mm, "p": page},
                    role=actor.role,
                ),
            )
            for hh, mm in chunk[i : i + 4]
        ]
        rows.append(row)
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb(
                    "sch_manual_time_end", {"p": page - 1}, role=actor.role
                ),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb(
                    "sch_manual_time_end", {"p": page + 1}, role=actor.role
                ),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()


@router.callback_query(_is("t", {"sch_manual_time_end_pick"}))
async def tui_sch_manual_time_end_pick(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    h = int(payload.get("h", 0))
    m = int(payload.get("m", 0))
    _manual_ctx_put(_uid(cq), {"eh": h, "em": m})
    return await tui_sch_manual_duration(cq, actor)


@router.callback_query(_is("t", {"sch_manual_time_end_min"}))
async def tui_sch_manual_time_end_min(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    # Deprecated handler: redirect to new flow if invoked
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    h = int(payload.get("h", 0)) if "h" in payload else 23
    m = int(payload.get("m", 0)) if "m" in payload else 50
    _manual_ctx_put(_uid(cq), {"eh": h, "em": m})
    return await tui_sch_manual_duration(cq, actor)


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
                text="10",
                callback_data=cb("sch_manual_capacity", {"dur": 10}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="15",
                callback_data=cb("sch_manual_capacity", {"dur": 15}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="20",
                callback_data=cb("sch_manual_capacity", {"dur": 20}, role=actor.role),
            ),
            types.InlineKeyboardButton(
                text="90",
                callback_data=cb("sch_manual_capacity", {"dur": 90}, role=actor.role),
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="Больше…",
                callback_data=cb("sch_manual_duration_more", {"p": 0}, role=actor.role),
            ),
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_duration", {})
    _stack_push(_uid(cq), "sch_manual_duration", {})


@router.callback_query(_is("t", {"sch_manual_duration_more"}))
async def tui_sch_manual_duration_more(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    page = int(payload.get("p", 0))
    options = list(range(20, 121, 5))
    per_page = 16
    total_pages = max(1, (len(options) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = options[start : start + per_page]
    text = "Шаг 5/7 — длительность. Расширенный список:"
    rows = []
    for i in range(0, len(chunk), 4):
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=str(d),
                    callback_data=cb(
                        "sch_manual_capacity", {"dur": d}, role=actor.role
                    ),
                )
                for d in chunk[i : i + 4]
            ]
        )
    nav = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb(
                    "sch_manual_duration_more", {"p": page - 1}, role=actor.role
                ),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb(
                    "sch_manual_duration_more", {"p": page + 1}, role=actor.role
                ),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_duration_more", {})


@router.callback_query(_is("t", {"sch_manual_capacity"}))
async def tui_sch_manual_capacity(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    dur = int(payload.get("dur", 0))
    if dur:
        _manual_ctx_put(_uid(cq), {"dur": dur})
    text = "Шаг 6/7 — вместимость. Выберите вместимость слотов:"
    # Decide by mode (online ≤3; offline ≤50)
    ctx = _manual_ctx_get(_uid(cq))
    mode = (ctx.get("mode") or "online").strip()
    rows = []
    base = [
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
    ]
    rows.append(base)
    if mode == "online":
        # online: only 1..3
        pass
    else:
        # offline: add "all" and precise selection
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="Все желающие (≤50)",
                    callback_data=cb(
                        "sch_manual_preview", {"cap": 50}, role=actor.role
                    ),
                )
            ]
        )
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="Точное значение…",
                    callback_data=cb(
                        "sch_manual_capacity_more", {"p": 0}, role=actor.role
                    ),
                )
            ]
        )
    rows.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_capacity", {})


@router.callback_query(_is("t", {"sch_manual_capacity_more"}))
async def tui_sch_manual_capacity_more(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    page = int(payload.get("p", 0))
    # precise values 1..50 (offline only, но хендлер не показывается в online)
    options = list(range(1, 51))
    per_page = 20
    total_pages = max(1, (len(options) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = options[start : start + per_page]
    text = "Шаг 6/7 — вместимость. Точный выбор:"
    rows = []
    for i in range(0, len(chunk), 5):
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=str(c),
                    callback_data=cb("sch_manual_preview", {"cap": c}, role=actor.role),
                )
                for c in chunk[i : i + 5]
            ]
        )
    nav = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb(
                    "sch_manual_capacity_more", {"p": page - 1}, role=actor.role
                ),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb(
                    "sch_manual_capacity_more", {"p": page + 1}, role=actor.role
                ),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_capacity_more", {})


@router.callback_query(_is("t", {"sch_manual_preview"}))
async def tui_sch_manual_preview(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    cap = int(payload.get("cap", 0))
    if cap:
        _manual_ctx_put(_uid(cq), {"cap": cap})
    ctx = _manual_ctx_get(_uid(cq))
    y, m, d = int(ctx.get("y", 1970)), int(ctx.get("m", 1)), int(ctx.get("d", 1))
    sh, sm = int(ctx.get("sh", 9)), int(ctx.get("sm", 0))
    eh, em = int(ctx.get("eh", sh + 1)), int(ctx.get("em", 0))
    dur = int(ctx.get("dur", 30))
    cap = int(ctx.get("cap", cap or 1))

    start_utc = _utc_ts(y, m, d, sh, sm)
    end_utc = _utc_ts(y, m, d, eh, em)
    total_min = max(0, (end_utc - start_utc) // 60)
    slots_cnt = max(0, total_min // max(1, dur))
    # total_min > 360 would indicate a >6h window, but we cap options earlier
    # Build preview with mode/location and conflicts
    # Count potential conflicts
    tries = generate_timeslots(start_utc, end_utc, dur)
    conflicts = 0
    with db() as conn:
        for s_ts in tries:
            e_ts = s_ts + dur * 60
            row = conn.execute(
                (
                    "SELECT 1 FROM slots WHERE created_by=? AND status IN ('open','closed') "
                    "AND starts_at_utc < ? AND (starts_at_utc + duration_min*60) > ? LIMIT 1"
                ),
                (actor.id, e_ts, s_ts),
            ).fetchone()
            if row:
                conflicts += 1
    mode = (_manual_ctx_get(_uid(cq)).get("mode") or "online").strip()
    location = (_manual_ctx_get(_uid(cq)).get("location") or "").strip()
    loc_disp = location if (mode == "online" or location) else "Аудитория по расписанию"
    text_parts = []
    text_parts.append("<b>Шаг 7/7 — предпросмотр</b>")
    # Unified time format
    from app.services.common.time_service import get_course_tz

    course_tz = get_course_tz()
    teacher_tz = _teacher_tz(actor)
    text_parts.append(
        f"🕒 <b>Начало:</b> {_format_dual_line(start_utc, course_tz, teacher_tz)}"
    )
    text_parts.append(
        f"🕘 <b>Окончание:</b> {_format_dual_line(end_utc, course_tz, teacher_tz)}"
    )
    text_parts.append(f"<b>Длительность:</b> {dur} мин")
    text_parts.append(f"<b>Вместимость:</b> {cap}")
    text_parts.append(f"<b>Формат:</b> {'Онлайн' if mode == 'online' else 'Очно'}")
    if location:
        if mode == "online":
            text_parts.append(f'<b>Ссылка:</b> <a href="{location}">Перейти</a>')
        else:
            text_parts.append(f"<b>Аудитория:</b> {loc_disp}")
    text_parts.append(f"<b>Слотов к созданию:</b> {slots_cnt}")
    if conflicts:
        text_parts.append("⚠️ <b>Конфликты:</b> " + str(conflicts))
    else:
        text_parts.append("✅ <b>Конфликтов нет</b>")
    text = "\n".join(text_parts)

    rows = [
        [
            types.InlineKeyboardButton(
                text="👁 Показать список слотов",
                callback_data=cb("sch_manual_list", role=actor.role),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="✅ Создать",
                callback_data=cb("sch_manual_create", role=actor.role),
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_preview", {})


@router.callback_query(_is("t", {"sch_manual_create"}))
async def tui_sch_manual_create(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    ctx = _manual_ctx_get(_uid(cq))
    try:
        y, m, d = int(ctx.get("y")), int(ctx.get("m")), int(ctx.get("d"))
        sh, sm = int(ctx.get("sh")), int(ctx.get("sm"))
        eh, em = int(ctx.get("eh")), int(ctx.get("em"))
        dur = int(ctx.get("dur"))
        cap = int(ctx.get("cap"))
    except Exception:
        return await cq.answer("⛔ Сессия истекла. Начните заново.", show_alert=True)
    start_utc = _utc_ts(y, m, d, sh, sm)
    end_utc = _utc_ts(y, m, d, eh, em)
    if end_utc <= start_utc:
        await _toast_error(
            cq, "E_INPUT_INVALID", "⛔ Время финиша должно быть позже старта"
        )
        return
    # hard cap for a single window: max 6 hours
    if (end_utc - start_utc) // 60 > 360:
        await _toast_error(cq, "E_DURATION_EXCEEDED")
        return
    if dur < 10 or dur > 240:
        await _toast_error(cq, "E_INPUT_INVALID", "⛔ Длительность вне диапазона")
        return
    # capacity constraints by mode
    mode = (ctx.get("mode") or "online").strip()
    if mode == "online" and cap > 3:
        await _toast_error(cq, "E_CAP_EXCEEDED")
        return
    if mode != "online" and cap > 50:
        await _toast_error(cq, "E_CAP_EXCEEDED")
        return

    # daily total cap (≤ 6h per UTC day)
    from datetime import datetime, timedelta, timezone

    day_start = datetime(y, m, d, 0, 0, tzinfo=timezone.utc)
    day_next = day_start + timedelta(days=1)
    day_start_utc = int(day_start.timestamp())
    day_next_utc = int(day_next.timestamp())

    tries = generate_timeslots(start_utc, end_utc, dur)
    new_minutes = len(tries) * dur
    existing_minutes = 0
    try:
        with db() as conn:
            row = conn.execute(
                (
                    "SELECT COALESCE(SUM(duration_min),0) FROM slots "
                    "WHERE created_by=? AND status IN ('open','closed') "
                    "AND starts_at_utc >= ? AND starts_at_utc < ?"
                ),
                (actor.id, day_start_utc, day_next_utc),
            ).fetchone()
            existing_minutes = int(row[0]) if row and row[0] is not None else 0
    except Exception:
        existing_minutes = 0
    if existing_minutes + new_minutes > 360:
        await _toast_error(cq, "E_DURATION_EXCEEDED")
        return

    # prepare location fallback for offline
    mode_val = str((_manual_ctx_get(_uid(cq)).get("mode") or "online"))
    loc_val = str((_manual_ctx_get(_uid(cq)).get("location") or ""))
    if mode_val != "online" and not loc_val:
        loc_val = "Аудитория по расписанию"
    created, skipped = create_slots_for_range(
        created_by=actor.id,
        start_utc=start_utc,
        end_utc=end_utc,
        duration_min=dur,
        capacity=cap,
        mode=mode_val,
        location=loc_val,
    )
    total_min = (end_utc - start_utc) // 60
    warn6h = total_min > 360
    try:
        msg = f"✅ Создано: {created} (пропущено: {skipped})"
        if skipped > 0:
            msg += "\n⚠️ Некоторые слоты пропущены (конфликт/дубликат)"
        if warn6h:
            msg = "⚠️ Превышен лимит 6 часов\n" + msg
        await cq.message.answer(msg)
    except Exception:
        pass
    toast = f"✅ Создано: {created} (пропущено: {skipped})"
    if skipped > 0:
        toast += " — ⚠️ пропуски из‑за конфликтов"
    await cq.answer(toast)

    # Cleanup session/state to avoid repeated creation from the same preview
    try:
        # Remove manual context and location awaiting state
        state_store.delete(_manual_ctx_key(_uid(cq)))
    except Exception:
        pass
    try:
        state_store.delete(_loc_key(_uid(cq)))
    except Exception:
        pass
    # Reset navigation stack and minimize active buttons on the preview message
    try:
        _stack_reset(_uid(cq))
    except Exception:
        pass
    try:
        await cq.message.edit_reply_markup(reply_markup=_nav_keyboard())
    except Exception:
        pass


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
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
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
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
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
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_preset_preview", {})


# ------- Schedule: Manage -------


def _weekday_name(dt) -> str:
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return names[int(dt.weekday()) % 7]


def _month_name(dt) -> str:
    names = [
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
    return names[int(dt.month) - 1]


def _sch_days_kb(
    role: str, page: int = 0, per_page: int = 8
) -> types.InlineKeyboardMarkup:
    from datetime import timedelta

    from app.services.common.time_service import course_today

    base = course_today()
    days = [base + timedelta(days=i) for i in range(0, 30)]
    total_pages = max(1, (len(days) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = days[start : start + per_page]
    rows: list[list[types.InlineKeyboardButton]] = []
    for d in chunk:
        label = f"{_weekday_name(d)}, {d.day:02d} {_month_name(d)}"
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=label,
                    callback_data=cb(
                        "sch_day", {"y": d.year, "m": d.month, "d": d.day}, role=role
                    ),
                )
            ]
        )
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад", callback_data=cb("sch_days", {"p": page - 1}, role=role)
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb("sch_days", {"p": page + 1}, role=role),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [
            types.InlineKeyboardButton(
                text="🗓 Все слоты",
                callback_data=cb("sch_manage_all", {"p": 0}, role=role),
            )
        ]
    )
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("t", {"sch_manage"}))
async def tui_sch_manage(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    from app.services.common.time_service import get_course_tz

    tz = get_course_tz()
    text = f"📅 <b>Управление расписанием</b>\nВыберите дату на ближайшие 30 дней.\n<i>Часовой пояс курса: {tz}</i>"
    kb = _sch_days_kb(actor.role, page=0)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manage", {})


@router.callback_query(_is("t", {"sch_days"}))
async def tui_sch_days(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    page = int(payload.get("p", 0))
    kb = _sch_days_kb(actor.role, page=page)
    try:
        await cq.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        from app.services.common.time_service import get_course_tz

        tz = get_course_tz()
        text = f"📅 <b>Управление расписанием</b>\nВыберите дату на ближайшие 30 дней.\n<i>Часовой пояс курса: {tz}</i>"
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()


def _slot_status_emoji(
    status: str, starts_at_utc: int, duration_min: int, booked: int, capacity: int
) -> str:
    from app.services.common.time_service import utc_now_ts

    now = utc_now_ts()
    if status == "canceled":
        return "🚫"
    if now >= int(starts_at_utc) + int(duration_min) * 60:
        return "⚫"
    if status == "closed":
        return "⚪"
    # open: occupancy based
    if booked <= 0:
        return "🟢"
    if booked < capacity:
        return "🟡"
    return "🔴"


def _format_hhmm(ts_utc: int, tz: str) -> str:
    from app.services.common.time_service import to_course_dt

    dt = to_course_dt(ts_utc, tz)
    return f"{dt.hour:02d}:{dt.minute:02d}"


def _format_dual_line(ts_utc: int, course_tz: str, user_tz: str | None) -> str:
    """YYYY-MM-DD HH:MM (TZ) · у вас сейчас ≈ HH:MM"""
    from app.services.common.time_service import to_course_dt

    cdt = to_course_dt(ts_utc, course_tz)
    base = f"{cdt.strftime('%Y-%m-%d %H:%M')} ({course_tz})"
    if user_tz and user_tz != course_tz:
        udt = to_course_dt(ts_utc, user_tz)
        return base + f" · у вас сейчас ≈ {udt.strftime('%H:%M')}"
    return base


def _slot_list_for_date_text(
    actor: Identity, y: int, m: int, d: int
) -> tuple[str, types.InlineKeyboardMarkup]:
    from app.services.common.time_service import get_course_tz, local_to_utc_ts

    course_tz = get_course_tz()
    start_utc = local_to_utc_ts(y, m, d, 0, 0, course_tz=course_tz)
    end_utc = (
        local_to_utc_ts(y, m, d, 23, 59, course_tz=course_tz) + 60
    )  # include 23:59
    rows: list[list[types.InlineKeyboardButton]] = []
    with db() as conn:
        # Detect optional columns
        cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)").fetchall()}
        has_mode = "mode" in cols
        has_location = "location" in cols
        q = "SELECT s.id, s.starts_at_utc, s.duration_min, s.capacity, s.status"
        if has_mode:
            q += ", s.mode"
        else:
            q += ", NULL as mode"
        if has_location:
            q += ", s.location"
        else:
            q += ", NULL as location"
        q += (
            ", COALESCE(SUM(CASE WHEN se.status='booked' THEN 1 ELSE 0 END),0) AS booked "
            "FROM slots s "
            "LEFT JOIN slot_enrollments se ON se.slot_id = s.id AND se.status='booked' "
            "WHERE s.created_by=? AND s.status IN ('open','closed') AND s.starts_at_utc >= ? AND s.starts_at_utc < ? "
            "GROUP BY s.id ORDER BY s.starts_at_utc ASC"
        )
        cur = conn.execute(q, (actor.id, start_utc, end_utc))
        rows_db = cur.fetchall()
    # Build buttons only; text is a compact card
    header = f"Слоты на дату {y:04d}-{m:02d}-{d:02d}"
    if not rows_db:
        text = header + "\n\n⛔ На выбранную дату слотов нет."
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[_nav_keyboard().inline_keyboard[0]]
        )
        return text, kb
    teacher_tz = _teacher_tz(actor)
    total = len(rows_db)

    # helper: mode emoji
    def _mode_emoji(mode: str) -> str:
        if mode == "online":
            return "🖥"
        if mode == "offline":
            return "🏫"
        return ""  # unknown/legacy

    for row in rows_db:
        sid, st, dur, cap, st_status, mode, _location, booked = (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            int(row[3]),
            str(row[4]),
            (str(row[5]) if row[5] is not None else ""),
            (str(row[6]) if row[6] is not None else ""),
            int(row[7]),
        )
        del _location
        emoji = _slot_status_emoji(st_status, st, dur, booked, cap)
        # Compact button: course time + optional local "ваш HH:MM", capacity and mode
        time_course = _format_hhmm(st, get_course_tz())
        time_local = (
            _format_hhmm(st, teacher_tz)
            if teacher_tz and teacher_tz != get_course_tz()
            else None
        )
        local_part = f" • ваш {time_local}" if time_local else ""
        cap_part = f" 👥{booked}/{cap}"
        mode_part = f" {_mode_emoji(mode)}" if mode else ""
        btn_text = f"{emoji} {time_course}{local_part}{cap_part}{mode_part}"
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=btn_text,
                    callback_data=cb("sch_slot", {"id": sid}, role=actor.role),
                )
            ]
        )
    # Summarize
    text = header + f"\n\nВсего слотов: <b>{total}</b>"
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=rows + [_nav_keyboard().inline_keyboard[0]]
    )
    return text, kb


@router.callback_query(_is("t", {"sch_manage_day"}))
async def tui_sch_manage_day(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    # read date from payload or last state
    y = m = d = None
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    choice = payload.get("d")
    if choice:
        y, m, d = _date_from_choice(str(choice))
    else:
        last = _stack_last_params(_uid(cq), "sch_manage_day") or {}
        y, m, d = int(last.get("y")), int(last.get("m")), int(last.get("d"))
    if not all([y, m, d]):
        # default to today
        y, m, d = _date_from_choice("today")
    text, kb = _slot_list_for_date_text(actor, int(y), int(m), int(d))
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manage_day", {"y": int(y), "m": int(m), "d": int(d)})


@router.callback_query(_is("t", {"sch_manage_all"}))
async def tui_sch_manage_all(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        payload = {}
    page = int(payload.get("p", 0))
    limit = 10
    offset = max(0, page) * limit
    from app.services.common.time_service import get_course_tz, utc_now_ts

    now = utc_now_ts()
    teacher_tz = _teacher_tz(actor)
    rows_btn: list[list[types.InlineKeyboardButton]] = []
    total = 0
    with db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)").fetchall()}
        has_mode = "mode" in cols
        has_location = "location" in cols
        q = "SELECT s.id, s.starts_at_utc, s.duration_min, s.capacity, s.status"
        q += ", COALESCE(b.cnt,0) as booked"
        if has_mode:
            q += ", s.mode"
        else:
            q += ", NULL as mode"
        if has_location:
            q += ", s.location"
        else:
            q += ", NULL as location"
        q += (
            " FROM slots s "
            " LEFT JOIN ("
            " SELECT slot_id, COUNT(1) AS cnt"
            " FROM slot_enrollments"
            " WHERE status='booked'"
            " GROUP BY slot_id"
            " ) b ON b.slot_id = s.id"
            " WHERE s.created_by=? AND s.status IN ('open','closed') AND s.starts_at_utc >= ?"
            " ORDER BY s.starts_at_utc ASC LIMIT ? OFFSET ?"
        )
        cur = conn.execute(q, (actor.id, now, limit, offset))
        rows_db = cur.fetchall()
        total_row = conn.execute(
            "SELECT COUNT(1) FROM slots WHERE created_by=? AND status IN ('open','closed') AND starts_at_utc >= ?",
            (actor.id, now),
        ).fetchone()
        total = int(total_row[0]) if total_row and total_row[0] is not None else 0

    # Beautify buttons like daily view; do not duplicate items in text
    def _mode_emoji(mode: str) -> str:
        if mode == "online":
            return "🖥"
        if mode == "offline":
            return "🏫"
        return ""

    for row in rows_db:
        sid = int(row[0])
        st = int(row[1])
        dur = int(row[2])
        cap = int(row[3])
        st_status = str(row[4])
        booked = int(row[5])
        mode = str(row[6]) if row[6] is not None else ""
        emoji = _slot_status_emoji(st_status, st, dur, booked, cap)
        # Compact button: course time + optional local "ваш HH:MM", capacity and mode
        time_course = _format_hhmm(st, get_course_tz())
        time_local = (
            _format_hhmm(st, teacher_tz)
            if teacher_tz and teacher_tz != get_course_tz()
            else None
        )
        local_part = f" • ваш {time_local}" if time_local else ""
        cap_part = f" 👥{booked}/{cap}"
        mode_part = f" {_mode_emoji(mode)}" if mode else ""
        btn_text = f"{emoji} {time_course}{local_part}{cap_part}{mode_part}"
        rows_btn.append(
            [
                types.InlineKeyboardButton(
                    text=btn_text,
                    callback_data=cb("sch_slot", {"id": sid}, role=actor.role),
                )
            ]
        )
    nav: list[types.InlineKeyboardButton] = []
    pages = max(1, (total + limit - 1) // limit)
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb("sch_manage_all", {"p": page - 1}, role=actor.role),
            )
        )
    if page < pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb("sch_manage_all", {"p": page + 1}, role=actor.role),
            )
        )
    if nav:
        rows_btn.append(nav)
    rows_btn.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows_btn)
    header = "📅 <b>Все предстоящие слоты</b>"
    if total == 0:
        text = header + "\n\n⛔ Слотов нет."
    else:
        pages = max(1, (total + limit - 1) // limit)
        text = (
            header
            + f"\nВсего: <b>{total}</b> • Стр. <b>{page + 1}</b>/<b>{pages}</b>\n<i>Часовой пояс курса: {get_course_tz()}</i>"
        )
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manage_all", {"p": page})


@router.callback_query(_is("t", {"sch_day"}))
async def tui_sch_day(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    y = int(payload.get("y"))
    m = int(payload.get("m"))
    d = int(payload.get("d"))
    # Reuse listing but do not duplicate items in text; show summary card
    from app.services.common.time_service import get_course_tz

    course_tz = get_course_tz()
    text, kb = _slot_list_for_date_text(actor, y, m, d)
    # Convert header to friendly card
    header = (
        f"📅 <b>{y:04d}-{m:02d}-{d:02d}</b>\n<i>Часовой пояс курса: {course_tz}</i>"
    )
    if "\n\n" in text:
        text = header + "\n\n" + text.split("\n\n", 1)[1]
    else:
        text = header
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_day", {"y": y, "m": m, "d": d})


def _slot_card(
    uid: int, actor: Identity, slot_id: int
) -> tuple[str, types.InlineKeyboardMarkup]:
    from app.services.common.time_service import get_course_tz, to_course_dt

    with db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)").fetchall()}
        has_mode = "mode" in cols
        has_location = "location" in cols
        q = "SELECT s.starts_at_utc, s.duration_min, s.capacity, s.status"
        if has_mode:
            q += ", s.mode"
        else:
            q += ", NULL as mode"
        if has_location:
            q += ", s.location"
        else:
            q += ", NULL as location"
        q += " FROM slots s WHERE s.id=? AND s.created_by=?"
        row = conn.execute(q, (slot_id, actor.id)).fetchone()
        if not row:
            text = "⛔ Слот не найден"
            kb = types.InlineKeyboardMarkup(
                inline_keyboard=[_nav_keyboard().inline_keyboard[0]]
            )
            return text, kb
        starts, dur, cap, status, mode, location = (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            str(row[3]),
            (str(row[4]) if row[4] is not None else ""),
            (str(row[5]) if row[5] is not None else ""),
        )
        booked = conn.execute(
            "SELECT COUNT(1) FROM slot_enrollments WHERE slot_id=? AND status='booked'",
            (slot_id,),
        ).fetchone()
        booked_n = int(booked[0]) if booked and booked[0] is not None else 0
    teacher_tz = _teacher_tz(actor)
    course_tz = get_course_tz()
    # Card-specific dual tz wording: "— а у вас будет: HH:MM"
    s_course_dt = to_course_dt(starts, course_tz)
    e_course_dt = to_course_dt(starts + dur * 60, course_tz)
    s_line = f"{s_course_dt.strftime('%Y-%m-%d %H:%M')} ({course_tz})"
    e_line = f"{e_course_dt.strftime('%Y-%m-%d %H:%M')} ({course_tz})"
    if teacher_tz and teacher_tz != course_tz:
        s_local_dt = to_course_dt(starts, teacher_tz)
        e_local_dt = to_course_dt(starts + dur * 60, teacher_tz)
        s_line += f" — <i>а у вас будет: {s_local_dt.strftime('%H:%M')}</i>"
        e_line += f" — <i>а у вас будет: {e_local_dt.strftime('%H:%M')}</i>"
    emoji = _slot_status_emoji(status, starts, dur, booked_n, cap)
    status_map = {
        "open": "Открыт",
        "closed": "Закрыт",
        "canceled": "Отменён",
    }
    st_label = status_map.get(status, status)
    lines = [
        f"<b>{emoji} Слот #{slot_id}</b>",
        f"🕒 <b>Начало:</b> {s_line}",
        f"🕘 <b>Окончание:</b> {e_line}",
        f"🔖 <b>Статус:</b> {st_label}",
        f"👥 <b>Заполнено:</b> {booked_n}/{cap}",
    ]
    if mode:
        mode_label = (
            "Онлайн" if mode == "online" else ("Очно" if mode == "offline" else mode)
        )
        mode_emoji = "🖥" if mode == "online" else ("🏫" if mode == "offline" else "")
        lines.append(f"{mode_emoji} <b>Формат:</b> {mode_label}")
    if location:
        lines.append(f"📍 <b>Место:</b> {location}")
    rows: list[list[types.InlineKeyboardButton]] = []
    if status != "canceled":
        if status == "open":
            rows.append(
                [
                    types.InlineKeyboardButton(
                        text="🚫 Закрыть",
                        callback_data=cb("sch_slot_toggle", {"id": slot_id}),
                    )
                ]
            )
        elif status == "closed":
            rows.append(
                [
                    types.InlineKeyboardButton(
                        text="✅ Открыть",
                        callback_data=cb("sch_slot_toggle", {"id": slot_id}),
                    )
                ]
            )
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=cb("sch_slot_delq", {"id": slot_id})
                )
            ]
        )
    rows.insert(
        0,
        [
            types.InlineKeyboardButton(
                text="👨‍🎓 Студенты",
                callback_data=cb("sch_slot_students", {"id": slot_id}, role=actor.role),
            )
        ],
    )
    rows.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    return "\n".join(lines), kb


@router.callback_query(_is("t", {"sch_slot"}))
async def tui_sch_slot(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    slot_id = int(payload.get("id"))
    text, kb = _slot_card(_uid(cq), actor, slot_id)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_slot", {"id": slot_id})


@router.callback_query(_is("t", {"sch_slot_students"}))
async def tui_sch_slot_students(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    slot_id = int(payload.get("id"))
    # List booked students for this slot
    with db() as conn:
        rows = conn.execute(
            (
                "SELECT u.id, COALESCE(u.name, u.tg_id) AS name "
                "FROM slot_enrollments e JOIN users u ON u.id = e.user_id "
                "WHERE e.slot_id=? AND e.status='booked' AND u.role='student' "
                "ORDER BY u.name"
            ),
            (slot_id,),
        ).fetchall()
    if not rows:
        text = "👨‍🎓 Студенты\n\n⛔ На этот слот никто не записан."
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=cb("sch_slot", {"id": slot_id}, role=actor.role),
                    )
                ]
            ]
        )
        try:
            await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await cq.answer()
        _stack_push(_uid(cq), "sch_slot_students", {"id": slot_id})
        return
    # Build list
    buttons = [
        [
            types.InlineKeyboardButton(
                text=f"👤 {str(r[1])[:64]}",
                callback_data=cb(
                    "sch_slot_student", {"sid": slot_id, "uid": r[0]}, role=actor.role
                ),
            )
        ]
        for r in rows
    ]
    buttons.append(_nav_keyboard().inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
    text = "👨‍🎓 <b>Студенты слота</b>\nВыберите студента для карточки."
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_slot_students", {"id": slot_id})


@router.callback_query(_is("t", {"sch_slot_student"}))
async def tui_sch_slot_student(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    slot_id = int(payload.get("sid"))
    user_id = str(payload.get("uid"))
    with db() as conn:
        row = conn.execute(
            "SELECT id, name, tg_id FROM users WHERE id=? AND role='student'",
            (user_id,),
        ).fetchone()
    if not row:
        return await _toast_error(cq, "E_NOT_FOUND", "⛔ Студент не найден")
    name = row[1] or row[2] or row[0]
    text = f"👤 <b>{name}</b>\nКарточка студента в этом слоте. Заглушка: детали и действия будут добавлены."
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=cb(
                        "sch_slot_students", {"id": slot_id}, role=actor.role
                    ),
                )
            ]
        ]
    )
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_slot_student", {"sid": slot_id, "uid": user_id})


@router.callback_query(_is("t", {"sch_slot_toggle"}))
async def tui_sch_slot_toggle(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    slot_id = int(payload.get("id"))
    with db() as conn:
        row = conn.execute(
            "SELECT status FROM slots WHERE id=? AND created_by=?",
            (slot_id, actor.id),
        ).fetchone()
        if not row:
            return await _toast_error(cq, "E_NOT_FOUND", "⛔ Слот не найден")
        cur = str(row[0])
        if cur == "canceled":
            return await _toast_error(cq, "E_STATE_INVALID", "⛔ Слот отменён")
        new = "closed" if cur == "open" else "open"
        conn.execute(
            "UPDATE slots SET status=?, created_at_utc=created_at_utc WHERE id=?",
            (new, slot_id),
        )
        conn.commit()
    await cq.answer("✅ Слот открыт" if new == "open" else "🚫 Слот закрыт")
    # refresh card
    text, kb = _slot_card(_uid(cq), actor, slot_id)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(_is("t", {"sch_slot_delq"}))
async def tui_sch_slot_delq(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    slot_id = int(payload.get("id"))
    rows = [
        [
            types.InlineKeyboardButton(
                text="🗑 Да, удалить", callback_data=cb("sch_slot_del", {"id": slot_id})
            )
        ],
        [
            types.InlineKeyboardButton(
                text="Отмена", callback_data=cb("sch_slot_del_cancel", {"id": slot_id})
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text("Удалить слот безвозвратно?", reply_markup=kb)
    except Exception:
        await cq.message.answer("Удалить слот безвозвратно?", reply_markup=kb)
    await cq.answer()


@router.callback_query(_is("t", {"sch_slot_del"}))
async def tui_sch_slot_del(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    slot_id = int(payload.get("id"))
    # Collect affected enrollments and slot datetime, then cancel slot and enrollments
    affected: list[tuple[int, str, int]] = []  # (enrollment_id, user_id, week_no)
    starts_at_utc: int | None = None
    with db() as conn:
        row = conn.execute(
            "SELECT starts_at_utc FROM slots WHERE id=? AND created_by=?",
            (slot_id, actor.id),
        ).fetchone()
        if not row:
            return await _toast_error(cq, "E_NOT_FOUND", "⛔ Слот не найден")
        starts_at_utc = int(row[0])
        # Take snapshot of active enrollments before update
        enr_rows = conn.execute(
            (
                "SELECT id, user_id, COALESCE(week_no, 0) FROM slot_enrollments "
                "WHERE slot_id=? AND status='booked'"
            ),
            (slot_id,),
        ).fetchall()
        affected = [(int(r[0]), str(r[1]), int(r[2] or 0)) for r in enr_rows]
        # Cancel the slot and associated active enrollments
        conn.execute(
            "UPDATE slots SET status='canceled' WHERE id=? AND created_by=?",
            (slot_id, actor.id),
        )
        if affected:
            conn.execute(
                "UPDATE slot_enrollments SET status='canceled' WHERE slot_id=? AND status='booked'",
                (slot_id,),
            )
        conn.commit()

    # Audit per enrollment and summary
    for eid, uid, wno in affected:
        try:
            audit.log(
                "STUDENT_BOOKING_AUTO_CANCEL",
                actor.id,
                object_type="slot_enrollment",
                object_id=int(eid),
                meta={
                    "slot_id": int(slot_id),
                    "week_no": int(wno),
                    "reason": "slot_canceled",
                },
            )
        except Exception:
            pass
    try:
        audit.log(
            "TEACHER_SLOT_CANCEL_NOTIFY",
            actor.id,
            object_type="slot",
            object_id=int(slot_id),
            meta={"affected": len(affected)},
        )
    except Exception:
        pass

    # Notify students about cancellation (best-effort)
    if affected and starts_at_utc is not None:
        try:
            from app.services.common.time_service import format_dual_tz, get_course_tz

            course_tz = get_course_tz()
        except Exception:
            course_tz = "UTC"
            format_dual_tz = None  # type: ignore
        for _eid, uid, _wno in affected:
            tg_id: str | None = None
            user_tz: str | None = None
            try:
                with db() as conn:
                    urow = conn.execute(
                        "SELECT tg_id, tz FROM users WHERE id=? AND role='student'",
                        (uid,),
                    ).fetchone()
                if urow:
                    tg_id = str(urow[0]) if urow[0] else None
                    user_tz = str(urow[1]) if urow[1] else None
            except Exception:
                tg_id = None
            if not tg_id:
                continue
            # Build message text
            try:
                if format_dual_tz is not None:
                    dt_line = format_dual_tz(
                        int(starts_at_utc), course_tz, user_tz or course_tz
                    )
                else:
                    dt_line = f"{starts_at_utc} ({course_tz})"
                msg = (
                    "❗ Ваш слот отменён преподавателем\n"
                    f"Дата/время: {dt_line}\n"
                    "Причина: слот удалён.\n"
                    "Вы можете выбрать другой слот в меню недели."
                )
                # send best-effort
                try:
                    bot = getattr(cq.message, "bot", None)
                    if bot and hasattr(bot, "send_message"):
                        await bot.send_message(tg_id, msg)
                except Exception:
                    pass
            except Exception:
                pass
    await cq.answer("🗑 Слот удалён")
    # Return to previous list if possible
    last_day = _stack_last_params(_uid(cq), "sch_manage_day")
    if last_day and all(k in last_day for k in ("y", "m", "d")):
        text, kb = _slot_list_for_date_text(
            actor, int(last_day["y"]), int(last_day["m"]), int(last_day["d"])
        )
        try:
            await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
        return
    # else fallback to manage root
    return await tui_sch_manage(cq, actor)


@router.callback_query(_is("t", {"sch_slot_del_cancel"}))
async def tui_sch_slot_del_cancel(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    slot_id = int(payload.get("id"))
    await cq.answer("🚪 Удаление отменено")
    # Return to slot card
    text, kb = _slot_card(_uid(cq), actor, slot_id)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")


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
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
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
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
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
    week_no = 0
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
        week_no = int(payload.get("week", 0))
    except Exception:
        p = _stack_last_params(_uid(cq), "materials_week") or {}
        week_no = int(p.get("week", 0))
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
    week_no = 0
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
        week_no = int(payload.get("week", 0))
    except Exception:
        p = _stack_last_params(_uid(cq), "materials_week") or {}
        week_no = int(p.get("week", 0))
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
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
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


@router.message(F.text, _awaits_manual_loc)
async def tui_manual_receive_location(m: types.Message, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return
    # Read awaited mode
    try:
        _, st = state_store.get(_loc_key(_uid(m)))
    except Exception:
        st = {}
    mode = (st.get("mode") or "online").strip()
    text = (m.text or "").strip()
    # Validate URL for online
    if mode == "online":
        from urllib.parse import urlparse

        u = urlparse(text)
        if not (u.scheme in ("http", "https") and u.netloc):
            await m.answer(
                "⛔ Некорректный URL. Пришлите ссылку вида https://... или нажмите Далее на шаге."
            )
            return
    # Save location
    _manual_ctx_put(_uid(m), {"location": text})
    try:
        state_store.delete(_loc_key(_uid(m)))
    except Exception:
        pass
    if mode == "online":
        try:
            await m.answer(
                "Шаг 3/7 — дата. Выберите дату для создания слотов:",
                reply_markup=_date_page_kb(actor.role, 0),
                parse_mode="HTML",
            )
        except Exception:
            pass
        _stack_push(_uid(m), "sch_manual_date", {})
        return
    if mode != "online" and text:
        try:
            await m.answer(
                "Шаг 3/7 — дата. Выберите дату для создания слотов:",
                reply_markup=_date_page_kb(actor.role, 0),
                parse_mode="HTML",
            )
        except Exception:
            pass
        _stack_push(_uid(m), "sch_manual_date", {})
        return


@router.callback_query(_is("t", {"sch_manual_place"}))
async def tui_sch_manual_place(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    mode = "online"
    try:
        _, payload = callbacks.extract(cq.data, expected_role=actor.role)
        mode = (payload.get("mode") or mode).strip()
    except Exception:
        ctx = _manual_ctx_get(_uid(cq))
        mode = (ctx.get("mode") or mode).strip()
    try:
        state_store.put_at(
            f"t_manual_ctx:{_uid(cq)}", "t_manual", {"mode": mode}, ttl_sec=900
        )
    except Exception:
        pass
    is_online = mode == "online"
    try:
        state_store.put_at(_loc_key(_uid(cq)), "t_loc", {"mode": mode}, ttl_sec=900)
    except Exception:
        pass
    loc = (_manual_ctx_get(_uid(cq)).get("location") or "").strip()
    if is_online:
        text = "<b>Шаг 2/7 — место проведения (онлайн)</b>\n"
        if loc:
            text += f'<b>Ссылка:</b> <a href="{loc}">Перейти</a>\n'
        else:
            text += "<b>Ссылка:</b> <i>не указана</i>\n"
        text += "Отправьте ссылку сообщением."
    else:
        text = "<b>Шаг 2/7 — место проведения (очно)</b>\n"
        if loc:
            text += f"<b>Аудитория:</b> {loc}\n"
        else:
            text += "<b>Аудитория:</b> <i>по расписанию (по умолчанию)</i>\n"
        text += 'Введите номер аудитории текстом или нажмите "Далее".'

    rows = []
    if not is_online:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="Далее",
                    callback_data=cb(
                        "sch_manual_date", {"mode": mode}, role=actor.role
                    ),
                )
            ]
        )
    rows.append(_nav_keyboard().inline_keyboard[0])

    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_place", {"mode": mode})


@router.callback_query(_is("t", {"sch_manual_list"}))
async def tui_sch_manual_list(cq: types.CallbackQuery, actor: Identity):
    if actor.role not in ("teacher", "owner"):
        return await cq.answer("Нет прав", show_alert=True)
    # derive current context
    ctx = _manual_ctx_get(_uid(cq))
    try:
        y, m, d = int(ctx.get("y")), int(ctx.get("m")), int(ctx.get("d"))
        sh, sm = int(ctx.get("sh")), int(ctx.get("sm"))
        eh, em = int(ctx.get("eh")), int(ctx.get("em"))
        dur = int(ctx.get("dur"))
    except Exception:
        return await cq.answer("⛔ Сессия истекла. Начните заново.", show_alert=True)
    start_utc = _utc_ts(y, m, d, sh, sm)
    end_utc = _utc_ts(y, m, d, eh, em)
    if end_utc <= start_utc or dur <= 0:
        return await cq.answer("⛔ Некорректный ввод", show_alert=True)
    # build list of times in HH:MM
    ts_list = generate_timeslots(start_utc, end_utc, dur)
    if not ts_list:
        text = "Список слотов пуст (проверьте параметры)"
    else:
        from app.services.common.time_service import get_course_tz, to_course_dt

        course_tz = get_course_tz()
        teacher_tz = _teacher_tz(actor)
        lines = []
        for s in ts_list:
            dt_course = to_course_dt(s, course_tz)
            label = f"{dt_course.hour:02d}:{dt_course.minute:02d}"
            if teacher_tz and teacher_tz != course_tz:
                dt_my = to_course_dt(s, teacher_tz)
                label += f" (у вас: {dt_my.hour:02d}:{dt_my.minute:02d})"
            lines.append(f"• {label}")
        text = "👁 Список слотов (время курса):\n" + "\n".join(lines)
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[_nav_keyboard().inline_keyboard[0]]
    )
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "sch_manual_list", {})
