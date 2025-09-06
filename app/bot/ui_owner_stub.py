from __future__ import annotations

import os

from aiogram.filters import Command, CommandStart

from aiogram import F, Router, types
from app.core import audit, callbacks, state_store
from app.core.auth import Identity, get_user_by_tg
from app.core.backup import backup_recent, trigger_backup
from app.core.config import cfg
from app.core.course_init import apply_course_init, parse_weeks_csv
from app.core.files import save_blob
from app.core.imports_epic5 import (
    E_DUPLICATE_USER,
    STUDENT_HEADERS,
    TEACHER_HEADERS,
    get_templates,
    get_users_summary,
    import_students_csv,
    import_teachers_csv,
)
from app.core.repos_epic4 import (
    archive_active,
    delete_archived,
    enforce_archive_limit,
    get_active_material,
    insert_week_material_file,
    list_material_versions,
    list_weeks,
)
from app.db.conn import db

router = Router(name="ui.owner.stub")


def _uid(x: types.Message | types.CallbackQuery) -> int:
    return x.from_user.id


def _imp_key(uid: int) -> str:
    return f"impersonate:{uid}"


def _nav_key(uid: int) -> str:
    return f"own_nav:{uid}"


def _now() -> int:
    return state_store.now()


def cb(action: str, params: dict | None = None) -> str:
    payload = {"action": action}
    if params:
        payload.update(params)
    return callbacks.build("own", payload, role="owner")


# Canonical assignment-matrix callbacks per L2: a=as; s=p|c
def _cb_as(step: str) -> str:
    return callbacks.build("own", {"a": "as", "s": step}, role="owner")


def _get_impersonation(uid: int) -> dict | None:
    try:
        _, payload = state_store.get(_imp_key(uid))
        return payload
    except Exception:
        return None


def _nav_keyboard(section: str = "root") -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=cb("back"),
                ),
                types.InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data=cb("home"),
                ),
            ]
        ]
    )


def _main_menu_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="⚙️ Управление курсом",
                    callback_data=cb("course"),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="👥 Люди и роли",
                    callback_data=cb("people"),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="📚 Материалы курса",
                    callback_data=cb("materials"),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="🗄️ Архив",
                    callback_data=cb("archive"),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="📊 Отчёты и аудит",
                    callback_data=cb("reports"),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="👤 Имперсонизация",
                    callback_data=cb("impersonation"),
                )
            ],
        ]
    )


async def _maybe_banner(uid: int) -> str:
    imp = _get_impersonation(uid)
    if not imp:
        return ""
    name = imp.get("name") or imp.get("tg_id")
    role = imp.get("role")
    exp = imp.get("exp")
    left = 0
    if isinstance(exp, int):
        left = max(0, (exp - _now() + 59) // 60)
    who = name or role
    return f"Вы действуете как {who}, осталось: {left} мин\n"


def _stack_get(uid: int) -> list[dict]:
    try:
        action, st = state_store.get(_nav_key(uid))
        if action != "own_nav":
            return []
        return st.get("stack") or []
    except Exception:
        return []


def _stack_set(uid: int, stack: list[dict]) -> None:
    state_store.put_at(_nav_key(uid), "own_nav", {"stack": stack}, ttl_sec=1800)


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


def _ci_key(uid: int) -> str:
    return f"own_ci:{uid}"


def _people_imp_key(uid: int) -> str:
    return f"own_imp:{uid}"


def _people_imp_ck(uid: int, kind: str) -> str:
    return f"own_imp_ck:{uid}:{kind}"


def _assign_key(uid: int) -> str:
    return f"own_assign:{uid}"


def _csv_filter_excess_columns(
    content: bytes, expected_headers: list[str]
) -> tuple[bytes, int, bool]:
    """
    Returns (filtered_csv_bytes, dropped_rows_count, headers_ok).
    - If headers don't match expected_headers, returns original content with 0 drops and headers_ok=False
    - Otherwise, drops data rows that have more columns than expected, keeping rows with <= expected.
    """
    import csv
    import io

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration:
        return content, 0, False
    if headers != expected_headers:
        return content, 0, False
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    dropped = 0
    for row in reader:
        if len(row) > len(expected_headers):
            dropped += 1
            continue
        # pad short rows to expected length
        if len(row) < len(expected_headers):
            row = row + [""] * (len(expected_headers) - len(row))
        w.writerow(row)
    return buf.getvalue().encode("utf-8"), dropped, True


@router.message(Command("owner"))
async def owner_menu_cmd(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return await m.answer("⛔ Доступ запрещён.")
    uid = _uid(m)
    _stack_reset(uid)
    banner = await _maybe_banner(uid)
    await m.answer(banner + "Главное меню", reply_markup=_main_menu_kb())


@router.message(Command("owner_menu"))
async def owner_menu_alt_cmd(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return await m.answer("⛔ Доступ запрещён.")
    uid = _uid(m)
    _stack_reset(uid)
    banner = await _maybe_banner(uid)
    await m.answer(banner + "Главное меню", reply_markup=_main_menu_kb())


@router.message(CommandStart())
async def owner_menu_on_start(m: types.Message, actor: Identity):
    # If already registered owner → show main menu automatically
    if actor.role != "owner":
        return
    uid = _uid(m)
    _stack_reset(uid)
    banner = await _maybe_banner(uid)
    await m.answer(banner + "Главное меню", reply_markup=_main_menu_kb())


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


def _is_as(step: str):
    """Predicate for assignment-matrix callbacks using canonical notation a=as;s=<step>."""

    def _f(cq: types.CallbackQuery) -> bool:
        try:
            op2, key = callbacks.parse(cq.data)
            if op2 != "own":
                return False
            _, payload = state_store.get(key)
            return payload.get("a") == "as" and payload.get("s") == step
        except Exception:
            return False

    return _f


@router.callback_query(_is("own", {"home"}))
async def ownui_home(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    # destroy-on-read (без использования params) — гасим токен
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    uid = _uid(cq)
    _stack_reset(uid)
    banner = await _maybe_banner(uid)
    try:
        await cq.message.edit_text(
            banner + "Главное меню", reply_markup=_main_menu_kb()
        )
    except Exception:
        await cq.message.answer(banner + "Главное меню", reply_markup=_main_menu_kb())
    await cq.answer()


@router.callback_query(_is("own", {"back"}))
async def ownui_back(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    prev = _stack_pop(uid)
    if not prev:
        return await ownui_home(cq, actor)
    screen = prev.get("s")
    params = prev.get("p") or {}
    if screen == "course":
        return await ownui_course(cq, actor)
    if screen == "people":
        return await ownui_people(cq, actor)
    if screen == "materials":
        return await ownui_materials(cq, actor)
    if screen == "materials_week":
        cq.data = cb("materials_week", {"week": params.get("week", 1)})
        return await ownui_materials_week(cq, actor)
    if screen == "archive":
        return await ownui_archive(cq, actor)
    if screen == "arch_materials_weeks":
        return await ownui_arch_materials(cq, actor)
    if screen == "arch_materials_versions":
        cq.data = cb("arch_materials_versions", {"week": params.get("week", 1)})
        return await ownui_arch_materials_versions(cq, actor)
    if screen == "arch_works_surname":
        return await ownui_arch_works(cq, actor)
    if screen == "arch_works_weeks":
        cq.data = cb("arch_works_weeks", {"surname": params.get("surname", "")})
        return await ownui_arch_works_weeks(cq, actor)
    if screen == "reports":
        return await ownui_reports(cq, actor)
    if screen == "imp":
        return await ownui_impersonation(cq, actor)
    if screen == "people_search":
        return await ownui_people_search_start(cq, actor)
    if screen == "ps_teachers":
        cq.data = cb("ps_t_list", {"p": params.get("page", 0)})
        return await ownui_ps_t_list(cq, actor)
    if screen == "ps_students_groups":
        cq.data = cb("ps_s_groups", {"p": params.get("page", 0)})
        return await ownui_ps_s_groups(cq, actor)
    if screen == "ps_students_names":
        cq.data = cb(
            "ps_s_names",
            {"g": params.get("g", ""), "p": params.get("page", 0)},
        )
        return await ownui_ps_s_names(cq, actor)
    return await ownui_home(cq, actor)


# -------- Course management --------


def _course_kb(disabled: bool) -> types.InlineKeyboardMarkup:
    init_btn = types.InlineKeyboardButton(
        text=("Инициализация курса" + (" 🔒" if disabled else "")),
        callback_data=cb("course_init"),
    )
    info_btn = types.InlineKeyboardButton(
        text="Общие сведения",
        callback_data=cb("course_info"),
    )
    rows = [
        [init_btn],
        [info_btn],
    ]
    rows.append(_nav_keyboard("course").inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("own", {"course"}))
async def ownui_course(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    imp = _get_impersonation(_uid(cq))
    disabled = bool(imp)
    header = "⚙️ Управление курсом"
    if disabled:
        header += "\n(Часть действий недоступна в режиме имперсонизации)"
    try:
        await cq.message.edit_text(header, reply_markup=_course_kb(disabled))
    except Exception:
        await cq.message.answer(header, reply_markup=_course_kb(disabled))
    await cq.answer()
    _stack_push(_uid(cq), "course", {})


def _fmt_deadline_utc(ts: int | None) -> tuple[str, str]:
    from datetime import datetime, timezone

    if not ts:
        # В спецификации для недель используется индикатор дедлайна (🟢/🔴).
        # Для отсутствующего дедлайна — без индикатора.
        return ("без дедлайна", "")
    # Для общих сведений показываем только дату (без времени и зоны)
    dlt = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
    indicator = "🟢" if ts >= _now() else "🔴"
    return (f"<b>дедлайн {dlt}</b>", indicator)


def _course_info_build(page: int = 0, per_page: int = 8) -> tuple[str, int, int]:
    # Returns (text, page, total_pages)
    with db() as conn:
        # course name (optional table)
        try:
            row = conn.execute("SELECT name FROM course WHERE id=1").fetchone()
            c_name = row[0] if row and row[0] else "(без названия)"
        except Exception:
            c_name = "(без названия)"
        total = conn.execute("SELECT COUNT(1) FROM weeks").fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        offset = page * per_page
        rows = conn.execute(
            "SELECT week_no, COALESCE(topic, title), deadline_ts_utc FROM weeks ORDER BY week_no ASC LIMIT ? OFFSET ?",
            (per_page, offset),
        ).fetchall()
    lines = [
        "📘 <b>Общие сведения о курсе</b>",
        f"<b>Название:</b> {c_name}",
        "",
        f"Структура курса (стр. {page + 1}/{total_pages})",
    ]
    for wno, topic, dl in rows:
        tp = topic or ""
        # В общих сведениях показываем фактический номер недели (без W-префикса)
        tag = f"{int(wno)}"
        dl_text, ind = _fmt_deadline_utc(dl)
        lines.append(f"• <b>Неделя {tag}</b> — {tp} — {dl_text} {ind}")
    if not rows:
        lines.append("• (нет недель)")
    return "\n".join(lines), page, total_pages


def _course_info_kb(page: int, total_pages: int) -> types.InlineKeyboardMarkup:
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb("course_info_page", {"page": page - 1}),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb("course_info_page", {"page": page + 1}),
            )
        )
    rows: list[list[types.InlineKeyboardButton]] = []
    if nav:
        rows.append(nav)
    rows.append(_nav_keyboard("course").inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("own", {"course_info"}))
async def ownui_course_info(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    # consume token
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    banner = await _maybe_banner(_uid(cq))
    text, page, total = _course_info_build(page=0)
    try:
        await cq.message.edit_text(
            banner + text, reply_markup=_course_info_kb(page, total), parse_mode="HTML"
        )
    except Exception:
        await cq.message.answer(
            banner + text, reply_markup=_course_info_kb(page, total)
        )
    await cq.answer()


@router.callback_query(_is("own", {"course_info_page"}))
async def ownui_course_info_page(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    p = int(payload.get("page", 0))
    banner = await _maybe_banner(_uid(cq))
    text, page, total = _course_info_build(page=p)
    try:
        await cq.message.edit_text(
            banner + text, reply_markup=_course_info_kb(page, total), parse_mode="HTML"
        )
    except Exception:
        await cq.message.answer(
            banner + text, reply_markup=_course_info_kb(page, total)
        )
    await cq.answer()


@router.callback_query(_is("own", {"course_init"}))
async def ownui_course_init(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    if _get_impersonation(_uid(cq)):
        return await cq.answer("⛔ Недоступно в режиме имперсонизации", show_alert=True)
    # Simulate multi-step: Параметры → weeks.csv → Подтверждение → Готово
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    # Prepare course-init state
    state_store.put_at(_ci_key(uid), "course_init", {"mode": "params"}, ttl_sec=1800)
    await cq.message.answer(
        banner + "Инициализация курса — шаг 1/3: Параметры\nВведите название курса:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Далее",
                        callback_data=cb("course_init_2"),
                    )
                ],
                _nav_keyboard("course").inline_keyboard[0],
            ]
        ),
    )
    await cq.answer()


@router.callback_query(_is("own", {"course_init_2"}))
async def ownui_course_init_2(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    # Ensure course name is set
    with db() as conn:
        row = conn.execute("SELECT name FROM course WHERE id=1").fetchone()
        cname = row["name"] if row else None
    if not cname or not str(cname).strip():
        await cq.message.answer(
            banner + "⛔ Сначала введите название курса",
            reply_markup=_nav_keyboard("course"),
        )
        return await cq.answer()
    state_store.put_at(_ci_key(uid), "course_init", {"mode": "await_csv"}, ttl_sec=1800)
    await cq.message.answer(
        banner
        + "Инициализация курса — шаг 2/3: Загрузите weeks.csv. Формат: week_id,topic,description,deadline",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Далее",
                        callback_data=cb("course_init_3"),
                    )
                ],
                _nav_keyboard("course").inline_keyboard[0],
            ]
        ),
    )
    await cq.answer()


def _awaits_ci_params(m: types.Message) -> bool:
    try:
        action, st = state_store.get(_ci_key(m.from_user.id))
    except Exception:
        return False
    return action == "course_init" and (st or {}).get("mode") == "params"


@router.message(F.text, _awaits_ci_params)
async def ownui_course_init_receive_name(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return
    name = (m.text or "").strip()
    if not name:
        return await m.answer("Название курса не может быть пустым")
    now = state_store.now()
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO course(id, name, created_at_utc, updated_at_utc) VALUES(1, ?, ?, ?)",
            (name, now, now),
        )
        conn.execute(
            "UPDATE course SET name=?, updated_at_utc=? WHERE id=1",
            (name, now),
        )
        conn.commit()
    # advance mode to saved
    uid = _uid(m)
    state_store.put_at(
        _ci_key(uid), "course_init", {"mode": "params_saved"}, ttl_sec=1800
    )
    await m.answer(f"✅ Название курса сохранено: {name}\nНажмите «Далее».")


def _awaits_ci_csv(m: types.Message) -> bool:
    try:
        action, st = state_store.get(_ci_key(m.from_user.id))
    except Exception:
        return False
    return action == "course_init" and (st or {}).get("mode") == "await_csv"


@router.message(F.document, _awaits_ci_csv)
async def ownui_course_init_receive_csv(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return
    uid = _uid(m)
    try:
        st_action, st = state_store.get(_ci_key(uid))
    except Exception:
        return
    if st_action != "course_init" or st.get("mode") != "await_csv":
        return
    doc = m.document
    file = await m.bot.get_file(doc.file_id)
    b = await m.bot.download_file(file.file_path)
    content = b.read()
    parsed = parse_weeks_csv(content)
    if parsed.errors:
        # Заголовки/структура CSV (синхронизировано с реестром: E_IMPORT_FORMAT)
        if any(e == "E_IMPORT_FORMAT" for e in parsed.errors):
            await m.answer("⛔ Ошибка формата CSV (лишние/неверные колонки)")
        # Невалидный дедлайн (контентная ошибка формата импорта)
        elif any(":E_IMPORT_FORMAT" in e and "deadline" in e for e in parsed.errors):
            await m.answer("⛔ Некорректная дата дедлайна")
        elif any("E_WEEK_DUPLICATE" in e for e in parsed.errors):
            await m.answer("⛔ Дубликаты идентификаторов недель (week_id)")
        elif any("E_WEEK_SEQUENCE_GAP" in e for e in parsed.errors):
            await m.answer(
                "⛔ Последовательность week_id должна быть непрерывной начиная с 1"
            )
        else:
            await m.answer("⛔ Ошибка CSV")
        state_store.put_at(
            _ci_key(uid), "course_init", {"mode": "await_csv"}, ttl_sec=1800
        )
        return
    rows = [
        {
            "week_no": r.week_no,
            "topic": r.topic,
            "description": r.description,
            "deadline_ts_utc": r.deadline_ts_utc,
        }
        for r in parsed.rows
    ]
    state_store.put_at(
        _ci_key(uid), "course_init", {"mode": "csv_ready", "rows": rows}, ttl_sec=1800
    )
    await m.answer("Файл принят. Перейдите к подтверждению (шаг 3/3).")


@router.callback_query(_is("own", {"course_init_3"}))
async def ownui_course_init_3(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    try:
        _, st = state_store.get(_ci_key(uid))
    except Exception:
        st = {}
    rows = (st or {}).get("rows") or []
    if not rows:
        await cq.message.answer(
            banner + "⛔ Сначала загрузите корректный weeks.csv.",
            reply_markup=_nav_keyboard("course"),
        )
        return await cq.answer()
    preview_lines = ["Предпросмотр недель:"]
    for r in rows[:10]:
        wn = r.get("week_no")
        tp = r.get("topic") or ""
        dl = r.get("deadline_ts_utc")
        if dl:
            from datetime import datetime, timezone

            dlt = datetime.fromtimestamp(int(dl), timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            preview_lines.append(f"– W{wn}: {tp} — дедлайн {dlt}")
        else:
            preview_lines.append(f"– W{wn}: {tp}")
    if len(rows) > 10:
        preview_lines.append(f"… и ещё {len(rows) - 10}")
    await cq.message.answer(
        banner
        + "Инициализация курса — шаг 3/3: Подтверждение\n"
        + "\n".join(preview_lines),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Подтвердить",
                        callback_data=cb("course_init_done"),
                    )
                ],
                _nav_keyboard("course").inline_keyboard[0],
            ]
        ),
    )
    await cq.answer()


@router.callback_query(_is("own", {"course_init_done"}))
async def ownui_course_init_done(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    try:
        _, st = state_store.get(_ci_key(uid))
    except Exception:
        st = {}
    rows = (st or {}).get("rows") or []
    if not rows:
        await cq.message.answer(
            banner + "⛔ Нет данных для применения. Загрузите weeks.csv.",
            reply_markup=_nav_keyboard("course"),
        )
        return await cq.answer()
    # Enforce backup freshness per L3
    if not backup_recent():
        await cq.message.answer(
            banner + "⛔ E_BACKUP_STALE — требуется свежий бэкап",
            reply_markup=_nav_keyboard("course"),
        )
        return await cq.answer()
    # Apply
    try:
        parsed = [
            # reconstruct WeekRow dicts; fields set in ownui_course_init_receive_csv
            # week_no, topic, description, deadline_ts_utc
            __import__("builtins").dict(**r)
            for r in rows  # type: ignore
        ]
        # Minimal safe apply
        from app.core.course_init import WeekRow as _WR

        apply_course_init([_WR(**r) for r in parsed])
        msg = "Готово. Инициализация завершена."
    except Exception:
        msg = "⛔ Не удалось применить инициализацию"
    try:
        state_store.delete(_ci_key(uid))
    except Exception:
        pass
    await cq.message.answer(banner + msg, reply_markup=_nav_keyboard("course"))
    await cq.answer()


# -------- People and roles --------


def _people_kb(impersonating: bool = False) -> types.InlineKeyboardMarkup:
    lock = " 🔒" if impersonating else ""
    rows = [
        [
            types.InlineKeyboardButton(
                text=f"Импорт студентов (CSV){lock}",
                callback_data=cb("people_imp_students"),
            ),
            types.InlineKeyboardButton(
                text=f"Импорт преподавателей (CSV){lock}",
                callback_data=cb("people_imp_teachers"),
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="Поиск профиля",
                callback_data=cb("people_search"),
            )
        ],
        [
            types.InlineKeyboardButton(
                text=f"Создать матрицу назначений{lock}",
                callback_data=_cb_as("p"),
            )
        ],
        _nav_keyboard("people").inline_keyboard,
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=[*rows[:-1], rows[-1][0]])


@router.callback_query(_is("own", {"people"}))
async def ownui_people(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    imp = bool(_get_impersonation(uid))
    await cq.message.answer(
        banner + "👥 Люди и роли", reply_markup=_people_kb(impersonating=imp)
    )
    await cq.answer()
    _stack_push(_uid(cq), "people", {})


@router.callback_query(
    _is(
        "own",
        {
            "people_matrix_stub",
        },
    )
)
async def ownui_people_stubs(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    act = payload.get("action")
    if act == "people_matrix" and _get_impersonation(_uid(cq)):
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + "⛔ Функция не реализована", reply_markup=_nav_keyboard("people")
    )
    await cq.answer()


# -------- People search --------


def _ps_key(uid: int) -> str:
    return f"own_ps:{uid}"


@router.callback_query(_is("own", {"people_search"}))
async def ownui_people_search_start(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Преподаватели",
                    callback_data=cb("ps_t_list", {"p": 0}),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Студенты",
                    callback_data=cb("ps_s_groups", {"p": 0}),
                )
            ],
            _nav_keyboard("people").inline_keyboard[0],
        ]
    )
    try:
        await cq.message.edit_text(
            banner + "Поиск профиля — выберите раздел:", reply_markup=kb
        )
    except Exception:
        await cq.message.answer(
            banner + "Поиск профиля — выберите раздел:", reply_markup=kb
        )
    await cq.answer()


def _awaits_ps_query(m: types.Message) -> bool:
    try:
        action, st = state_store.get(_ps_key(m.from_user.id))
    except Exception:
        return False
    return action == "people_search" and (st or {}).get("mode") == "await_query"


@router.message(F.text, _awaits_ps_query)
async def ownui_people_search_query(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return
    q = (m.text or "").strip()
    if not q:
        return await m.answer("Введите хотя бы 1 символ")
    uid = _uid(m)
    # store last query (optional)
    try:
        state_store.put_at(
            _ps_key(uid), "people_search", {"mode": "query_entered"}, ttl_sec=900
        )
    except Exception:
        pass
    like = q.lower() + "%"
    rows: list[dict] = []
    with db() as conn:
        cur = conn.execute(
            (
                "SELECT id, role, name, COALESCE(email,''), COALESCE(group_name,''), tef, capacity, tg_id "
                "FROM users WHERE LOWER(COALESCE(name,'')) LIKE ? OR LOWER(COALESCE(email,'')) LIKE ? "
                "ORDER BY role, name LIMIT 20"
            ),
            (like, like),
        )
        for r in cur.fetchall():
            rows.append(
                {
                    "id": str(r[0]),
                    "role": r[1] or "",
                    "name": r[2] or "",
                    "email": r[3] or "",
                    "group_name": r[4] or "",
                    "tef": r[5],
                    "capacity": r[6],
                    "tg_id": r[7],
                }
            )
    if not rows:
        return await m.answer("Ничего не найдено", reply_markup=_nav_keyboard("people"))
    kb_rows: list[list[types.InlineKeyboardButton]] = []
    for r in rows:
        extra = ""
        if r["role"] == "student" and r["group_name"]:
            extra = f" — {r['group_name']}"
        if r["role"] == "teacher" and r["capacity"] is not None:
            extra = f" — cap {r['capacity']}"
        kb_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{r['name']} ({r['role']}){extra}",
                    callback_data=cb("people_profile", {"uid": r["id"]}),
                )
            ]
        )
    kb_rows.append(_nav_keyboard("people").inline_keyboard[0])
    await m.answer(
        "Найдено по текстовому поиску:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.callback_query(_is("own", {"people_profile"}))
async def ownui_people_profile(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    uid_param = str(payload.get("uid", ""))
    if not uid_param:
        return await cq.answer("Некорректный пользователь", show_alert=True)
    row = None
    with db() as conn:
        row = conn.execute(
            (
                "SELECT id, role, name, email, group_name, tef, capacity, tg_id, is_active "
                "FROM users WHERE id=? LIMIT 1"
            ),
            (uid_param,),
        ).fetchone()
    if not row:
        return await cq.answer("Пользователь не найден", show_alert=True)
    role = row[1] or ""
    name = row[2] or "(без имени)"
    email = row[3] or "—"
    group_name = row[4] or ""
    capacity = row[6]
    tg_bound = bool(row[7])
    active = int(row[8] or 0) == 1
    role_emoji = (
        "👑"
        if role == "owner"
        else ("👨‍🏫" if role == "teacher" else ("🎓" if role == "student" else "👤"))
    )
    status_emoji = "🟢" if active else "⚪️"
    tg_emoji = "🟢" if tg_bound else "⚪️"
    lines = [
        f"<b>{name}</b>",
        f"<b>Роль:</b> {role_emoji} {role}",
        f"<b>Email:</b> {email}",
        f"<b>Статус:</b> {status_emoji} {'активен' if active else 'неактивен'}",
    ]
    if role == "student":
        lines.append(f"<b>Группа:</b> {group_name or '—'}")
    if role == "teacher":
        lines.append(
            f"<b>Максимум студентов:</b> {capacity if capacity is not None else '—'}"
        )
    lines.append(f"<b>TG:</b> {tg_emoji} {'привязан' if tg_bound else 'не привязан'}")
    banner = await _maybe_banner(_uid(cq))
    toggle_txt = "Сделать неактивным" if active else "Сделать активным"
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=toggle_txt,
                    callback_data=cb("ps_toggle_active", {"uid": uid_param}),
                )
            ],
            _nav_keyboard("people").inline_keyboard[0],
        ]
    )
    try:
        await cq.message.edit_text(
            banner + "\n".join(lines), reply_markup=kb, parse_mode="HTML"
        )
    except Exception:
        await cq.message.answer(
            banner + "\n".join(lines), reply_markup=kb, parse_mode="HTML"
        )
    await cq.answer()


@router.callback_query(_is("own", {"ps_toggle_active"}))
async def ownui_people_toggle_active(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    if _get_impersonation(_uid(cq)):
        return await cq.answer("⛔ Недоступно в режиме имперсонизации", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    uid_param = str(payload.get("uid", ""))
    if not uid_param:
        return await cq.answer("Некорректный пользователь", show_alert=True)
    with db() as conn:
        row = conn.execute(
            "SELECT role, name, email, group_name, tef, capacity, tg_id, is_active FROM users WHERE id=? LIMIT 1",
            (uid_param,),
        ).fetchone()
        if not row:
            return await cq.answer("Пользователь не найден", show_alert=True)
        cur = int(row[7] or 0)
        new_val = 0 if cur == 1 else 1
        conn.execute(
            "UPDATE users SET is_active=?, updated_at_utc=strftime('%s','now') WHERE id=?",
            (new_val, uid_param),
        )
        conn.commit()
        # Fetch updated row
        row = conn.execute(
            "SELECT role, name, email, group_name, tef, capacity, tg_id, is_active FROM users WHERE id=? LIMIT 1",
            (uid_param,),
        ).fetchone()
    # Rebuild card (same format as ownui_people_profile)
    role = row[0] or ""
    name = row[1] or "(без имени)"
    email = row[2] or "—"
    group_name = row[3] or ""
    capacity = row[5]
    tg_bound = bool(row[6])
    active = int(row[7] or 0) == 1
    role_emoji = (
        "👑"
        if role == "owner"
        else ("👨‍🏫" if role == "teacher" else ("🎓" if role == "student" else "👤"))
    )
    status_emoji = "🟢" if active else "⚪️"
    tg_emoji = "🟢" if tg_bound else "⚪️"
    lines = [
        f"<b>{name}</b>",
        f"<b>Роль:</b> {role_emoji} {role}",
        f"<b>Email:</b> {email}",
        f"<b>Статус:</b> {status_emoji} {'активен' if active else 'неактивен'}",
    ]
    if role == "student":
        lines.append(f"<b>Группа:</b> {group_name or '—'}")
    if role == "teacher":
        lines.append(
            f"<b>Максимум студентов:</b> {capacity if capacity is not None else '—'}"
        )
    lines.append(f"<b>TG:</b> {tg_emoji} {'привязан' if tg_bound else 'не привязан'}")
    toggle_txt = "Сделать неактивным" if active else "Сделать активным"
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=toggle_txt,
                    callback_data=cb("ps_toggle_active", {"uid": uid_param}),
                )
            ],
            _nav_keyboard("people").inline_keyboard[0],
        ]
    )
    banner = await _maybe_banner(_uid(cq))
    try:
        await cq.message.edit_text(
            banner + "\n".join(lines), reply_markup=kb, parse_mode="HTML"
        )
    except Exception:
        await cq.message.answer(
            banner + "\n".join(lines), reply_markup=kb, parse_mode="HTML"
        )
    return await cq.answer()


@router.callback_query(_is("own", {"ps_t_list"}))
async def ownui_ps_t_list(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    page = int(payload.get("p", 0))
    per_page = 10
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(1) FROM users WHERE role='teacher'"
        ).fetchone()[0]
        total_pages = max(1, (int(total) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        offset = page * per_page
        rows = conn.execute(
            (
                "SELECT id, name, capacity FROM users WHERE role='teacher' "
                "ORDER BY COALESCE(name,'') ASC LIMIT ? OFFSET ?"
            ),
            (per_page, offset),
        ).fetchall()
    kb_rows: list[list[types.InlineKeyboardButton]] = []
    for r in rows:
        tid = str(r[0])
        name = r[1] or "(без имени)"
        cap = r[2]
        cap_txt = f" — cap {cap}" if cap is not None else ""
        kb_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{name}{cap_txt}",
                    callback_data=cb("people_profile", {"uid": tid}),
                )
            ]
        )
    pager = []
    if page > 0:
        pager.append(
            types.InlineKeyboardButton(
                text="◀", callback_data=cb("ps_t_list", {"p": page - 1})
            )
        )
    pager.append(
        types.InlineKeyboardButton(
            text=f"{page + 1}/{max(1, total_pages)}", callback_data=cb("noop")
        )
    )
    if page < total_pages - 1:
        pager.append(
            types.InlineKeyboardButton(
                text="▶", callback_data=cb("ps_t_list", {"p": page + 1})
            )
        )
    kb_rows.append(pager)
    kb_rows.append(_nav_keyboard("people").inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)
    try:
        await cq.message.edit_text(banner + "Преподаватели:", reply_markup=kb)
    except Exception:
        await cq.message.answer(banner + "Преподаватели:", reply_markup=kb)
    _stack_push(uid, "ps_teachers", {"page": page})
    await cq.answer()


@router.callback_query(_is("own", {"ps_s_groups"}))
async def ownui_ps_s_groups(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    page = int(payload.get("p", 0))
    per_page = 10
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    with db() as conn:
        groups = [
            r[0]
            for r in conn.execute(
                (
                    "SELECT DISTINCT group_name FROM users WHERE role='student' AND COALESCE(group_name,'')<>'' "
                    "ORDER BY group_name ASC"
                )
            ).fetchall()
        ]
    total = len(groups)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = groups[start : start + per_page]
    kb_rows: list[list[types.InlineKeyboardButton]] = []
    for g in chunk:
        kb_rows.append(
            [
                types.InlineKeyboardButton(
                    text=g,
                    callback_data=cb("ps_s_names", {"g": g, "p": 0}),
                )
            ]
        )
    pager = []
    if page > 0:
        pager.append(
            types.InlineKeyboardButton(
                text="◀", callback_data=cb("ps_s_groups", {"p": page - 1})
            )
        )
    pager.append(
        types.InlineKeyboardButton(
            text=f"{page + 1}/{max(1, total_pages)}", callback_data=cb("noop")
        )
    )
    if page < total_pages - 1:
        pager.append(
            types.InlineKeyboardButton(
                text="▶", callback_data=cb("ps_s_groups", {"p": page + 1})
            )
        )
    kb_rows.append(pager)
    kb_rows.append(_nav_keyboard("people").inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)
    try:
        await cq.message.edit_text(banner + "Группы студентов:", reply_markup=kb)
    except Exception:
        await cq.message.answer(banner + "Группы студентов:", reply_markup=kb)
    _stack_push(uid, "ps_students_groups", {"page": page})
    await cq.answer()


@router.callback_query(_is("own", {"ps_s_names"}))
async def ownui_ps_s_names(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    group = payload.get("g") or ""
    page = int(payload.get("p", 0))
    per_page = 10
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(1) FROM users WHERE role='student' AND COALESCE(group_name,'')=?",
            (group,),
        ).fetchone()[0]
        total_pages = max(1, (int(total) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        offset = page * per_page
        rows = conn.execute(
            (
                "SELECT id, name FROM users WHERE role='student' AND COALESCE(group_name,'')=? "
                "ORDER BY COALESCE(name,'') ASC LIMIT ? OFFSET ?"
            ),
            (group, per_page, offset),
        ).fetchall()
    kb_rows: list[list[types.InlineKeyboardButton]] = []
    for r in rows:
        sid = str(r[0])
        name = r[1] or "(без имени)"
        kb_rows.append(
            [
                types.InlineKeyboardButton(
                    text=name,
                    callback_data=cb("people_profile", {"uid": sid}),
                )
            ]
        )
    pager = []
    if page > 0:
        pager.append(
            types.InlineKeyboardButton(
                text="◀", callback_data=cb("ps_s_names", {"g": group, "p": page - 1})
            )
        )
    pager.append(
        types.InlineKeyboardButton(
            text=f"{page + 1}/{max(1, total_pages)}", callback_data=cb("noop")
        )
    )
    if page < total_pages - 1:
        pager.append(
            types.InlineKeyboardButton(
                text="▶", callback_data=cb("ps_s_names", {"g": group, "p": page + 1})
            )
        )
    kb_rows.append(pager)
    kb_rows.append(_nav_keyboard("people").inline_keyboard[0])
    kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)
    header = f"Студенты группы {group}:"
    try:
        await cq.message.edit_text(banner + header, reply_markup=kb)
    except Exception:
        await cq.message.answer(banner + header, reply_markup=kb)
    _stack_push(uid, "ps_students_names", {"g": group, "page": page})
    await cq.answer()


# Import: People (students/teachers)


def _awaits_imp(m: types.Message, kind: str) -> bool:
    try:
        action, st = state_store.get(_people_imp_key(m.from_user.id))
    except Exception:
        return False
    return action == kind and (st or {}).get("mode") == "await_csv"


@router.callback_query(_is("own", {"people_imp_students"}))
async def ownui_people_imp_students(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    if _get_impersonation(_uid(cq)):
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    state_store.put_at(
        _people_imp_key(uid), "imp_students", {"mode": "await_csv"}, ttl_sec=1800
    )
    text = (
        "Импорт студентов — загрузите файл students.csv.\n"
        "Формат: surname,name,patronymic,email,group_name"
    )
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Скачать шаблон",
                    callback_data=cb("people_tpl", {"t": "students"}),
                )
            ],
            _nav_keyboard("people").inline_keyboard[0],
        ]
    )
    try:
        await cq.message.edit_text(banner + text, reply_markup=kb)
    except Exception:
        await cq.message.answer(banner + text, reply_markup=kb)
    await cq.answer()


@router.callback_query(_is("own", {"people_imp_teachers"}))
async def ownui_people_imp_teachers(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    if _get_impersonation(_uid(cq)):
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    uid = _uid(cq)
    banner = await _maybe_banner(uid)
    state_store.put_at(
        _people_imp_key(uid), "imp_teachers", {"mode": "await_csv"}, ttl_sec=1800
    )
    text = (
        "Импорт преподавателей — загрузите файл teachers.csv.\n"
        "Формат: surname,name,patronymic,email,tef,capacity"
    )
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Скачать шаблон",
                    callback_data=cb("people_tpl", {"t": "teachers"}),
                )
            ],
            _nav_keyboard("people").inline_keyboard[0],
        ]
    )
    try:
        await cq.message.edit_text(banner + text, reply_markup=kb)
    except Exception:
        await cq.message.answer(banner + text, reply_markup=kb)
    await cq.answer()


@router.callback_query(_is("own", {"people_tpl"}))
async def ownui_people_tpl(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    t = (payload.get("t") or "").lower()
    tpls = get_templates()
    name = "teachers.csv" if t == "teachers" else "students.csv"
    data = tpls.get(name)
    if not data:
        return await cq.answer("Шаблон недоступен", show_alert=True)
    await cq.message.answer_document(
        types.BufferedInputFile(data, filename=name),
        caption="Шаблон CSV",
    )
    await cq.answer()


@router.message(F.document, lambda m: _awaits_imp(m, "imp_students"))
async def ownui_people_imp_students_receive(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return
    uid = _uid(m)
    doc = m.document
    file = await m.bot.get_file(doc.file_id)
    b = await m.bot.download_file(file.file_path)
    content = b.read()
    # Deduplicate by checksum per kind (do before state checks to show edge toast)
    import hashlib

    ck = hashlib.sha256(content).hexdigest()
    try:
        prev_ck_action, prev_ck = state_store.get(_people_imp_ck(uid, "students"))
    except Exception:
        prev_ck_action, prev_ck = None, None
    if prev_ck_action == "ck" and prev_ck == ck:
        await m.answer("⚠️ Импорт дублируется по checksum — пропущено")
        return
    state_store.put_at(_people_imp_ck(uid, "students"), "ck", ck, ttl_sec=3600)
    # Ensure we are in the awaited state
    try:
        st_action, st = state_store.get(_people_imp_key(uid))
    except Exception:
        return
    if st_action != "imp_students" or st.get("mode") != "await_csv":
        return

    # Filter rows with extra columns and warn
    filtered, dropped, headers_ok = _csv_filter_excess_columns(content, STUDENT_HEADERS)
    if headers_ok and dropped > 0:
        await m.answer(f"⚠️ Лишние колонки CSV — строки проигнорированы: {dropped}")
    content = filtered
    res = import_students_csv(content)
    total = res.created + res.updated
    summary = f"Импорт завершён: {total} строк, ошибок {len(res.errors)}"
    await m.answer(summary)
    # Created/Updated breakdown
    await m.answer(f"Создано: {res.created}, обновлено: {res.updated}")
    # Duplicate rows in CSV
    dups = sum(1 for e in res.errors if len(e) >= 3 and e[2] == E_DUPLICATE_USER)
    if dups:
        await m.answer(f"⚠️ Дубликаты в файле: {dups} — строки пропущены")
    # Users summary
    us = get_users_summary()
    us_text = (
        f"Учителя: всего {us.get('teachers_total', 0)}, без TG {us.get('teachers_no_tg', 0)}\n"
        f"Студенты: всего {us.get('students_total', 0)}, без TG {us.get('students_no_tg', 0)}"
    )
    await m.answer(us_text, reply_markup=_nav_keyboard("people"))
    if res.errors:
        err_csv = res.to_error_csv()
        await m.answer_document(
            types.BufferedInputFile(err_csv, filename="students_import_errors.csv"),
            caption="Ошибки импорта",
        )
    try:
        state_store.delete(_people_imp_key(uid))
    except Exception:
        pass


@router.message(F.document, lambda m: _awaits_imp(m, "imp_teachers"))
async def ownui_people_imp_teachers_receive(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return
    uid = _uid(m)
    doc = m.document
    file = await m.bot.get_file(doc.file_id)
    b = await m.bot.download_file(file.file_path)
    content = b.read()
    # Deduplicate by checksum per kind (do before state checks to show edge toast)
    import hashlib

    ck = hashlib.sha256(content).hexdigest()
    try:
        prev_ck_action, prev_ck = state_store.get(_people_imp_ck(uid, "teachers"))
    except Exception:
        prev_ck_action, prev_ck = None, None
    if prev_ck_action == "ck" and prev_ck == ck:
        await m.answer("⚠️ Импорт дублируется по checksum — пропущено")
        return
    state_store.put_at(_people_imp_ck(uid, "teachers"), "ck", ck, ttl_sec=3600)
    # Ensure we are in the awaited state
    try:
        st_action, st = state_store.get(_people_imp_key(uid))
    except Exception:
        return
    if st_action != "imp_teachers" or st.get("mode") != "await_csv":
        return

    # Filter rows with extra columns and warn
    filtered, dropped, headers_ok = _csv_filter_excess_columns(content, TEACHER_HEADERS)
    if headers_ok and dropped > 0:
        await m.answer(f"⚠️ Лишние колонки CSV — строки проигнорированы: {dropped}")
    content = filtered
    res = import_teachers_csv(content)
    total = res.created + res.updated
    summary = f"Импорт завершён: {total} строк, ошибок {len(res.errors)}"
    await m.answer(summary)
    # Created/Updated breakdown
    await m.answer(f"Создано: {res.created}, обновлено: {res.updated}")
    # Duplicate rows in CSV
    dups = sum(1 for e in res.errors if len(e) >= 3 and e[2] == E_DUPLICATE_USER)
    if dups:
        await m.answer(f"⚠️ Дубликаты в файле: {dups} — строки пропущены")
    # Users summary
    us = get_users_summary()
    us_text = (
        f"Учителя: всего {us.get('teachers_total', 0)}, без TG {us.get('teachers_no_tg', 0)}\n"
        f"Студенты: всего {us.get('students_total', 0)}, без TG {us.get('students_no_tg', 0)}"
    )
    await m.answer(us_text, reply_markup=_nav_keyboard("people"))
    if res.errors:
        err_csv = res.to_error_csv()
        await m.answer_document(
            types.BufferedInputFile(err_csv, filename="teachers_import_errors.csv"),
            caption="Ошибки импорта",
        )
    try:
        state_store.delete(_people_imp_key(uid))
    except Exception:
        pass


# -------- Materials --------


def _materials_weeks_kb(page: int = 0) -> types.InlineKeyboardMarkup:
    # 28 per page, 7 columns; read weeks from DB
    weeks = list_weeks(limit=200)
    per_page = 28
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
                callback_data=cb("materials_week", {"week": n}),
            )
        )
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=cb("materials_page", {"page": page - 1}),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=cb("materials_page", {"page": page + 1}),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(_nav_keyboard("materials").inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("own", {"materials"}))
async def ownui_materials(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    # гасим токен
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + "📚 Материалы курса: выберите неделю",
        reply_markup=_materials_weeks_kb(),
    )
    await cq.answer()
    _stack_push(_uid(cq), "materials", {})


@router.callback_query(_is("own", {"materials_page"}))
async def ownui_materials_page(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    page = int(payload.get("page", 0))
    try:
        await cq.message.edit_reply_markup(reply_markup=_materials_weeks_kb(page))
    except Exception:
        banner = await _maybe_banner(_uid(cq))
        await cq.message.answer(
            banner + "📚 Материалы курса: выберите неделю",
            reply_markup=_materials_weeks_kb(page),
        )
    await cq.answer()


def _materials_types_kb(week: int) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="📖 Домашние задачи и материалы для подготовки",
                callback_data=cb("mat_type", {"t": "p", "w": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📘 Методические рекомендации",
                callback_data=cb("mat_type", {"t": "m", "w": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📝 Конспект",
                callback_data=cb("mat_type", {"t": "n", "w": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📊 Презентация",
                callback_data=cb("mat_type", {"t": "s", "w": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="🎥 Записи лекций",
                callback_data=cb("mat_type", {"t": "v", "w": week}),
            )
        ],
        _nav_keyboard("materials").inline_keyboard,
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=[*rows[:-1], rows[-1][0]])


@router.callback_query(_is("own", {"materials_week"}))
async def ownui_materials_week(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("week", 0))
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + f"Неделя W{week}: выберите тип материала",
        reply_markup=_materials_types_kb(week),
    )
    await cq.answer()
    _stack_push(_uid(cq), "materials_week", {"week": week})


# --- Materials: helpers/state ---

try:
    from aiogram.types import BufferedInputFile  # aiogram v3
except Exception:  # pragma: no cover
    BufferedInputFile = None  # type: ignore


def _mat_key(uid: int) -> str:
    return f"own_mat:{uid}"


def _week_id_by_no(week_no: int) -> int | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM weeks WHERE week_no=?", (week_no,)
        ).fetchone()
        return int(row[0]) if row else None


def _visibility_for_type(t: str) -> str:
    # By convention: methodical ('m') is teacher-only, others public
    return "teacher_only" if t == "m" else "public"


def _mat_type_label(t: str) -> tuple[str, str]:
    mapping = {
        "p": ("📖", "Домашние задачи и материалы для подготовки"),
        "m": ("📘", "Методические рекомендации"),
        "n": ("📝", "Конспект"),
        "s": ("📊", "Презентация"),
        "v": ("🎥", "Записи лекций"),
    }
    return mapping.get(t, ("📄", "Материал"))


def _fmt_bytes(n: int | None) -> str:
    if not n:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    s = float(n)
    i = 0
    while s >= 1024 and i < len(units) - 1:
        s /= 1024.0
        i += 1
    return f"{s:.1f} {units[i]}"


def _material_card_kb(
    week: int, t: str, impersonating: bool
) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="⬆️ Загрузить" + (" 🔒" if impersonating else ""),
                callback_data=cb("mat_upload", {"w": week, "t": t}),
            ),
            types.InlineKeyboardButton(
                text="📂 Скачать активное",
                callback_data=cb("mat_download", {"w": week, "t": t}),
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="🕓 История",
                callback_data=cb("mat_history", {"w": week, "t": t}),
            ),
            types.InlineKeyboardButton(
                text="🗄️ В архив" + (" 🔒" if impersonating else ""),
                callback_data=cb("mat_archive", {"w": week, "t": t}),
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="🗑️ Удалить (из архива)" + (" 🔒" if impersonating else ""),
                callback_data=cb("mat_delete", {"w": week, "t": t}),
            )
        ],
        _nav_keyboard("materials").inline_keyboard,
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=[*rows[:-1], rows[-1][0]])


@router.callback_query(_is("own", {"mat_type"}))
async def ownui_material_type(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("w", 0))
    t = payload.get("t", "?")
    imp = _get_impersonation(_uid(cq))
    banner = await _maybe_banner(_uid(cq))
    # Card header with human-friendly info
    emoji, label = _mat_type_label(t)
    wk_id = _week_id_by_no(week)
    active_line = "<i>Активной версии нет</i>"
    if wk_id is not None:
        mat = get_active_material(wk_id, t)
        if mat:
            fname = os.path.basename(mat.path or "") or "—"
            size = _fmt_bytes(int(mat.size_bytes or 0))
            active_line = f"<b>Активная:</b> {fname} · v{mat.version} · {size}"
    header = f"<b>{emoji} {label}</b>\n" f"<b>Неделя:</b> W{week}\n" f"{active_line}"
    await cq.message.answer(
        banner + header,
        reply_markup=_material_card_kb(week, t, impersonating=bool(imp)),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(_is("own", {"mat_upload"}))
async def ownui_mat_upload(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    imp = _get_impersonation(_uid(cq))
    if imp:
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("w", 0))
    t = payload.get("t", "p")
    if t == "v":
        # Video material is a link upload (cloud/YouTube). Put a stub for now.
        return await cq.answer("Загрузка ссылок на видео — заглушка", show_alert=True)
    # set state to await document
    state_store.put_at(
        _mat_key(_uid(cq)),
        "own_mat",
        {"mode": "await_doc", "w": week, "t": t},
        ttl_sec=900,
    )
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(banner + "Отправьте документ для загрузки (один файл)")
    await cq.answer()


def _awaits_mat_doc(m: types.Message) -> bool:
    try:
        act, st = state_store.get(_mat_key(m.from_user.id))
        return act == "own_mat" and (st or {}).get("mode") == "await_doc"
    except Exception:
        return False


@router.message(F.document, _awaits_mat_doc)
async def ownui_mat_receive_doc(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return
    # get state
    try:
        _, st = state_store.get(_mat_key(_uid(m)))
    except Exception:
        return
    week = int(st.get("w", 0))
    t = str(st.get("t", "p"))
    doc = m.document
    # Validate size limit before download
    try:
        fsz = int(getattr(doc, "file_size", 0) or 0)
    except Exception:
        fsz = 0
    limit_bytes = int(cfg.max_file_mb) * 1024 * 1024
    if fsz and fsz > limit_bytes:
        return await m.answer(
            f"⛔ E_SIZE_LIMIT — Превышен лимит: ≤{cfg.max_file_mb} МБ"
        )
    # Validate extension/mime by type
    fname = (doc.file_name or "").lower()
    ext = "." + fname.split(".")[-1] if "." in fname else ""
    allowed_by_type = {
        "p": {".pdf"},
        "m": {".pdf"},
        "n": {".pdf"},
        "s": {".pdf", ".ppt", ".pptx"},
    }
    if t != "v":
        allowed = allowed_by_type.get(t, {".pdf"})
        if ext not in allowed:
            return await m.answer(
                "⛔ E_INPUT_INVALID — Недопустимый тип файла для материала"
            )
    # Proceed to download
    file = await m.bot.get_file(doc.file_id)
    b = await m.bot.download_file(file.file_path)
    data = b.read()
    saved = save_blob(
        data, prefix="materials", suggested_name=doc.file_name or "material.bin"
    )
    vis = _visibility_for_type(t)
    mid = insert_week_material_file(
        week_no=week,
        uploaded_by=actor.id,
        path=saved.path,
        sha256=saved.sha256,
        size_bytes=saved.size_bytes,
        mime=doc.mime_type,
        visibility=vis,
        type=t,
        original_name=doc.file_name or None,
    )
    if mid == -1:
        await m.answer(
            "⚠️ Файл идентичен активной версии или уже существует — загрузка пропущена"
        )
        return
    # Enforce archive limit after backup if needed
    wk_id = _week_id_by_no(week)
    if wk_id is not None:
        with db() as conn:
            row = conn.execute(
                "SELECT COUNT(1) FROM materials WHERE week_id=? AND type=?",
                (wk_id, t),
            ).fetchone()
            total = int(row[0] or 0)
        if total > 20:
            try:
                trigger_backup("auto")
            except Exception:
                pass
            removed = enforce_archive_limit(wk_id, t, max_versions=20)
            if removed > 0:
                await m.answer(f"⚠️ Удалены старые архивные версии: {removed}")
    # Audit and notify
    try:
        wk_id = _week_id_by_no(week)
        mat = get_active_material(wk_id, t) if wk_id is not None else None
        audit.log(
            "OWNER_MATERIAL_UPLOAD",
            actor.id,
            meta={
                "week": week,
                "type": t,
                "size_bytes": int(saved.size_bytes),
                "sha256": saved.sha256,
                "version": int(getattr(mat, "version", 0) or 0),
            },
        )
    except Exception:
        pass
    await m.answer("✅ Загрузка завершена")
    # keep state for possible next upload or clear? clear state
    state_store.delete(_mat_key(_uid(m)))


@router.callback_query(_is("own", {"mat_download"}))
async def ownui_mat_download(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("w", 0))
    t = payload.get("t", "p")
    wk_id = _week_id_by_no(week)
    if wk_id is None:
        return await cq.answer("Неделя не найдена", show_alert=True)
    mat = get_active_material(wk_id, t)
    if not mat or not BufferedInputFile:
        return await cq.answer("Нет активной версии", show_alert=True)
    try:
        with open(mat.path, "rb") as f:
            data = f.read()
        await cq.message.answer_document(
            BufferedInputFile(
                data, filename=(mat.path.split("/")[-1] or f"W{week}_{t}.bin")
            ),
            caption=f"W{week} {t}: активная версия v{mat.version}",
        )
        try:
            audit.log(
                "OWNER_MATERIAL_DOWNLOAD",
                actor.id,
                meta={"week": week, "type": t, "version": int(mat.version or 0)},
            )
        except Exception:
            pass
    except Exception:
        return await cq.answer("Не удалось подготовить файл", show_alert=True)
    await cq.answer()


@router.callback_query(_is("own", {"mat_history"}))
async def ownui_mat_history(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("w", 0))
    t = payload.get("t", "p")
    wk_id = _week_id_by_no(week)
    if wk_id is None:
        return await cq.answer("Неделя не найдена", show_alert=True)
    items = list_material_versions(wk_id, t, limit=20)
    emoji, label = _mat_type_label(t)
    lines = [f"<b>{emoji} История — {label}</b>", f"<b>Неделя:</b> W{week}"]
    for it in items:
        status = "активна" if int(it.is_active or 0) == 1 else "архив"
        fname = os.path.basename(it.path or "") or "—"
        size = _fmt_bytes(int(it.size_bytes or 0))
        lines.append(f"• v{it.version} — {status} — {fname} — {size}")
    if len(lines) == 1:
        lines.append("• (версий нет)")
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(banner + "\n".join(lines), parse_mode="HTML")
    await cq.answer()


@router.callback_query(_is("own", {"mat_archive"}))
async def ownui_mat_archive(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    imp = _get_impersonation(_uid(cq))
    if imp:
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("w", 0))
    t = payload.get("t", "p")
    wk_id = _week_id_by_no(week)
    if wk_id is None:
        return await cq.answer("Неделя не найдена", show_alert=True)
    # audit which version is being archived
    prev = get_active_material(wk_id, t)
    changed = archive_active(wk_id, t)
    await cq.answer()
    if changed:
        await cq.message.answer("✅ Активная версия отправлена в архив")
        try:
            audit.log(
                "OWNER_MATERIAL_ARCHIVE",
                actor.id,
                meta={
                    "week": week,
                    "type": t,
                    "version": int(getattr(prev, "version", 0) or 0),
                },
            )
        except Exception:
            pass
    else:
        await cq.message.answer("Нет активной версии")


@router.callback_query(_is("own", {"mat_delete"}))
async def ownui_mat_delete(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    imp = _get_impersonation(_uid(cq))
    if imp:
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("w", 0))
    t = payload.get("t", "p")
    wk_id = _week_id_by_no(week)
    if wk_id is None:
        return await cq.answer("Неделя не найдена", show_alert=True)
    # backup then delete all archived for this type
    try:
        trigger_backup("auto")
    except Exception:
        pass
    deleted = delete_archived(wk_id, t)
    await cq.message.answer(f"Удалено архивных версий: {deleted}")
    try:
        audit.log(
            "OWNER_MATERIAL_DELETE_ARCHIVED",
            actor.id,
            meta={"week": week, "type": t, "deleted": int(deleted)},
        )
    except Exception:
        pass
    await cq.answer()


# -------- Archive --------


def _archive_kb() -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="Материалы",
                callback_data=cb("arch_materials"),
            ),
            types.InlineKeyboardButton(
                text="Работы студентов",
                callback_data=cb("arch_works"),
            ),
        ],
        _nav_keyboard("archive").inline_keyboard,
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=[rows[0], rows[1][0]])


@router.callback_query(_is("own", {"archive"}))
async def ownui_archive(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(banner + "🗄️ Архив", reply_markup=_archive_kb())
    await cq.answer()
    _stack_push(_uid(cq), "archive", {})


@router.callback_query(_is("own", {"arch_materials"}))
async def ownui_arch_materials(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + "Архив материалов: выберите неделю", reply_markup=_materials_weeks_kb()
    )
    await cq.answer()
    _stack_push(_uid(cq), "arch_materials_weeks", {})


@router.callback_query(_is("own", {"arch_materials_versions"}))
async def ownui_arch_materials_versions(cq: types.CallbackQuery, actor: Identity):
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("week", 0))
    imp = _get_impersonation(_uid(cq))
    lock = " 🔒" if imp else ""
    rows = [
        [
            types.InlineKeyboardButton(
                text="📂 Скачать всё",
                callback_data=cb("arch_download_all", {"week": week}),
            ),
            types.InlineKeyboardButton(
                text=f"🗑️ Удалить всё{lock}",
                callback_data=cb("arch_delete_all", {"week": week}),
            ),
        ],
        _nav_keyboard("archive").inline_keyboard[0],
    ]
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + f"Архив W{week}: версии",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cq.answer()
    _stack_push(_uid(cq), "arch_materials_versions", {"week": week})


@router.callback_query(_is("own", {"arch_download_all"}))
async def ownui_arch_download_all(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("week", 0))
    wk_id = _week_id_by_no(week)
    if wk_id is None or BufferedInputFile is None:
        return await cq.answer("Неделя не найдена", show_alert=True)
    # Collect archived files for the week (all types)
    with db() as conn:
        rows = conn.execute(
            "SELECT path, type, version FROM materials WHERE week_id=? AND is_active=0 ORDER BY type ASC, version DESC",
            (wk_id,),
        ).fetchall()
    if not rows:
        return await cq.answer("Архив пуст", show_alert=True)
    import io
    import os
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, t, ver in rows:
            try:
                name = os.path.basename(path) or f"W{week}_{t}_v{ver}.bin"
                arcname = f"W{week}/{t}/v{ver}/{name}"
                tar.add(path, arcname=arcname)
            except Exception:
                continue
    buf.seek(0)
    await cq.message.answer_document(
        BufferedInputFile(buf.read(), filename=f"W{week}_materials_archive.tar.gz"),
        caption=f"Архив W{week}: {len(rows)} файлов",
    )
    try:
        audit.log(
            "OWNER_MATERIAL_ARCHIVE_DOWNLOAD_ALL",
            actor.id,
            meta={"week": week, "files": len(rows)},
        )
    except Exception:
        pass
    await cq.answer()


@router.callback_query(_is("own", {"arch_delete_all"}))
async def ownui_arch_delete_all(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    imp = _get_impersonation(_uid(cq))
    if imp:
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("week", 0))
    wk_id = _week_id_by_no(week)
    if wk_id is None:
        return await cq.answer("Неделя не найдена", show_alert=True)
    # Backup then delete all archived across types for this week
    try:
        trigger_backup("auto")
    except Exception:
        pass
    deleted = delete_archived(wk_id, None)
    await cq.message.answer(f"Удалено архивных версий (все типы): {deleted}")
    try:
        audit.log(
            "OWNER_MATERIAL_ARCHIVE_DELETE_ALL",
            actor.id,
            meta={"week": week, "deleted": int(deleted)},
        )
    except Exception:
        pass
    await cq.answer()


@router.callback_query(_is("own", {"materials_week"}))
async def ownui_arch_materials_choose_week(cq: types.CallbackQuery, actor: Identity):
    # Branch only when previous was archive week selection
    st = _stack_get(_uid(cq))
    if not st or st[-1].get("s") not in ("arch_materials_weeks", "arch_works_weeks"):
        return  # handled elsewhere
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("week", 0))
    if st[-1].get("s") == "arch_materials_weeks":
        cq.data = cb("arch_materials_versions", {"week": week})
        return await ownui_arch_materials_versions(cq, actor)
    # Works flow
    surname = st[-1].get("p", {}).get("surname", "")
    cq.data = cb("arch_works_week", {"week": week, "surname": surname})
    return await ownui_arch_works_week(cq, actor)


@router.callback_query(_is("own", {"arch_works"}))
async def ownui_arch_works(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + "Архив работ студентов: введите фамилию",
        reply_markup=_nav_keyboard("archive"),
    )
    await cq.answer()
    _stack_push(_uid(cq), "arch_works_surname", {})


@router.callback_query(_is("own", {"arch_works_weeks"}))
async def ownui_arch_works_weeks(cq: types.CallbackQuery, actor: Identity):
    # Re-render weeks prompt after Back
    st = _stack_get(_uid(cq))
    surname = st[-1].get("p", {}).get("surname", "") if st else ""
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + f"Найдено: {surname} (заглушка). Выберите неделю",
        reply_markup=_materials_weeks_kb(),
    )
    await cq.answer()


@router.callback_query(_is("own", {"arch_works_week"}))
async def ownui_arch_works_week(cq: types.CallbackQuery, actor: Identity):
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week = int(payload.get("week", 0))
    imp = _get_impersonation(_uid(cq))
    lock = " 🔒" if imp else ""
    rows = [
        [
            types.InlineKeyboardButton(
                text="📂 Скачать всё",
                callback_data=cb("arch_download_all", {"week": week}),
            ),
            types.InlineKeyboardButton(
                text=f"🗑️ Удалить всё{lock}",
                callback_data=cb("arch_delete_all", {"week": week}),
            ),
        ],
        _nav_keyboard("archive").inline_keyboard[0],
    ]
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + f"Работы: {payload.get('surname', '')} W{week} (заглушка)",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cq.answer()
    _stack_push(
        _uid(cq),
        "arch_works_week",
        {"week": week, "surname": payload.get("surname", "")},
    )


@router.callback_query(_is("own", {"arch_download_all", "arch_delete_all"}))
async def ownui_arch_bulk_actions(cq: types.CallbackQuery, actor: Identity):
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    act = payload.get("action")
    if act == "arch_delete_all" and _get_impersonation(_uid(cq)):
        return await cq.answer(
            "⛔ Безвозвратное удаление недоступно в режиме имперсонизации",
            show_alert=True,
        )
    await cq.answer("⛔ Функция не реализована", show_alert=True)


# -------- Reports --------


def _reports_kb(impersonating: bool) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="📥 Экспорт AuditLog",
                callback_data=cb("rep_audit"),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📥 Экспорт оценок",
                callback_data=cb("rep_grades"),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📥 Экспорт assignment matrix",
                callback_data=cb("rep_matrix"),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📥 Экспорт курса",
                callback_data=cb("rep_course"),
            )
        ],
        [
            types.InlineKeyboardButton(
                text=("📦 Запустить бэкап сейчас" + (" 🔒" if impersonating else "")),
                callback_data=cb("rep_backup"),
            )
        ],
        _nav_keyboard("reports").inline_keyboard,
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=[*rows[:-1], rows[-1][0]])


@router.callback_query(_is("own", {"reports"}))
async def ownui_reports(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    imp = _get_impersonation(_uid(cq))
    lines = [
        "📊 Отчёты и аудит",
        "Плановый бэкап ежедневно в 03:00 UTC",
    ]
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + "\n".join(lines), reply_markup=_reports_kb(bool(imp))
    )
    await cq.answer()
    _stack_push(_uid(cq), "reports", {})


@router.callback_query(_is("own", {"rep_audit", "rep_grades", "rep_course"}))
async def ownui_reports_stubs(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    # гасим токен
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    # Политика бэкапов: требуем свежий бэкап для тяжёлых операций экспорта
    if not backup_recent():
        return await cq.answer("⛔ Недоступно: нет свежего бэкапа", show_alert=True)
    await cq.answer("⛔ Функция не реализована", show_alert=True)


@router.callback_query(_is("own", {"rep_backup"}))
async def ownui_report_backup(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    # гасим токен
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    if _get_impersonation(_uid(cq)):
        return await cq.answer(
            "⛔ Бэкап недоступен в режиме имперсонизации", show_alert=True
        )
    # Запускаем авто-бэкап (full если требуется, иначе incremental)
    try:
        trigger_backup("auto")
        await cq.answer("✅ Бэкап запущен", show_alert=True)
    except Exception as e:
        await cq.answer(f"⛔ Не удалось выполнить бэкап: {e}", show_alert=True)


# -------- Impersonation --------


def _impersonation_idle_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Продолжить",
                    callback_data=cb("imp_start"),
                )
            ],
            _nav_keyboard("imp").inline_keyboard[0:1][0],
        ]
    )


def _impersonation_active_kb(role: str) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    if role == "student":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🎓 Главное меню студента",
                    callback_data=cb("imp_student_menu"),
                )
            ]
        )
    if role == "teacher":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="📚 Главное меню преподавателя",
                    callback_data=cb("imp_teacher_menu"),
                )
            ]
        )
    rows.append(
        [
            types.InlineKeyboardButton(
                text="↩️ Завершить имперсонизацию",
                callback_data=cb("imp_stop"),
            )
        ]
    )
    rows.append(_nav_keyboard("imp").inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is("own", {"impersonation"}))
async def ownui_impersonation(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    imp = _get_impersonation(_uid(cq))
    if not imp:
        banner = await _maybe_banner(_uid(cq))
        await cq.message.answer(
            banner + "Введите tg_id для имперсонизации",
            reply_markup=_impersonation_idle_kb(),
        )
    else:
        banner = await _maybe_banner(_uid(cq))
        await cq.message.answer(
            banner, reply_markup=_impersonation_active_kb(imp.get("role", ""))
        )
    await cq.answer()
    _stack_push(_uid(cq), "imp", {})


@router.callback_query(_is("own", {"imp_start"}))
async def ownui_impersonation_start(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    state_store.put_at(
        _imp_key(uid),
        "imp_setup",
        {"mode": "expect_tg", "exp": _now() + 1800},
        ttl_sec=1800,
    )
    banner = await _maybe_banner(uid)
    await cq.message.answer(
        banner + "Введите tg_id:", reply_markup=_nav_keyboard("imp")
    )
    await cq.answer()


def _awaits_imp_tg(m: types.Message) -> bool:
    try:
        action, st = state_store.get(_imp_key(m.from_user.id))
    except Exception:
        return False
    return action == "imp_setup" and st.get("mode") == "expect_tg"


@router.message(F.text, _awaits_imp_tg)
async def ownui_impersonation_receive(m: types.Message, actor: Identity):
    # Only attempt to interpret when owner and waiting for tg
    if actor.role != "owner":
        return
    uid = _uid(m)
    tg = (m.text or "").strip()
    u = get_user_by_tg(tg)
    if not u:
        await m.answer("❌ Пользователь не найден.")
        return
    state_store.put_at(
        _imp_key(uid),
        "imp_active",
        {"tg_id": tg, "role": u.role, "name": u.name, "exp": _now() + 1800},
        ttl_sec=1800,
    )
    banner = await _maybe_banner(uid)
    await m.answer(banner, reply_markup=_impersonation_active_kb(u.role))


@router.callback_query(_is("own", {"imp_student_menu", "imp_teacher_menu"}))
async def ownui_impersonation_menus(cq: types.CallbackQuery, actor: Identity):
    await cq.answer("⛔ Функция не реализована", show_alert=True)


@router.callback_query(_is("own", {"imp_stop"}))
async def ownui_impersonation_stop(cq: types.CallbackQuery, actor: Identity):
    try:
        state_store.delete(_imp_key(_uid(cq)))
    except Exception:
        pass
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + "Имперсонизация завершена.", reply_markup=_nav_keyboard("imp")
    )
    await cq.answer()


# -------- Assignment matrix (preview/commit) --------


@router.callback_query(_is_as("p"))
async def ownui_people_matrix_preview(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    # Extract to consume token and check impersonation
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    if _get_impersonation(uid):
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    # Build preview
    import uuid

    with db() as conn:
        weeks = [
            r[0]
            for r in conn.execute(
                "SELECT week_no FROM weeks ORDER BY week_no ASC"
            ).fetchall()
        ]
        students = conn.execute(
            (
                "SELECT id, COALESCE(name,''), COALESCE(group_name,'') "
                "FROM users "
                "WHERE role='student' AND (is_active IS NULL OR is_active=1) "
                "ORDER BY COALESCE(group_name,''), COALESCE(name,''), id"
            )
        ).fetchall()
        # Teachers include classic teachers and owner acting as teacher (capacity > 0)
        teachers = conn.execute(
            (
                "SELECT id, COALESCE(name,''), COALESCE(capacity,0) FROM users "
                "WHERE (role='teacher' OR (role='owner' AND tg_id=?)) "
                "AND (is_active IS NULL OR is_active=1) AND COALESCE(capacity,0) > 0 "
                "ORDER BY COALESCE(name,''), id"
            ),
            (actor.tg_id,),
        ).fetchall()
    if not weeks:
        return await cq.answer("⛔ Недоступно: нет недель курса", show_alert=True)
    if not students:
        return await cq.answer("⛔ Недоступно: нет студентов", show_alert=True)
    if not teachers:
        return await cq.answer(
            "⛔ Недоступно: нет преподавателей с положительным лимитом", show_alert=True
        )
    total_cap = sum(int(t[2]) for t in teachers)
    if total_cap < len(students):
        try:
            audit.log(
                "OWNER_ASSIGN_PREVIEW",
                actor.id,
                request_id=str(uuid.uuid4()),
                meta={
                    "error": "INSUFFICIENT_CAPACITY",
                    "teachers": len(teachers),
                    "weeks": len(weeks),
                    "students": len(students),
                    "total_capacity": total_cap,
                },
            )
        except Exception:
            pass
        return await cq.answer(
            f"⛔ Недостаточная суммарная вместимость преподавателей: {total_cap} < {len(students)}",
            show_alert=True,
        )

    # Round-robin per week with rotation
    def _assign_for_week(week_index: int):
        remaining = {t[0]: int(t[2]) for t in teachers}
        order = [t[0] for t in teachers]
        pos = week_index % len(order)
        result = []  # list of (student_id, teacher_id)
        for sid, _, _ in students:
            tried = 0
            while tried < len(order) and remaining[order[pos]] <= 0:
                pos = (pos + 1) % len(order)
                tried += 1
            if tried >= len(order):
                break
            tid = order[pos]
            result.append((sid, tid))
            remaining[tid] -= 1
            pos = (pos + 1) % len(order)
        return result

    matrix = []  # list of dicts: {week_no, student_id, teacher_id}
    for wi, w in enumerate(weeks):
        pairs = _assign_for_week(wi)
        for sid, tid in pairs:
            matrix.append({"week_no": int(w), "student_id": sid, "teacher_id": tid})
    # Save to StateStore for commit
    req_id = str(uuid.uuid4())
    state_store.put_at(
        _assign_key(uid),
        "assign_preview",
        {
            "req": req_id,
            "weeks": weeks,
            "students": [s[0] for s in students],
            "teachers": [t[0] for t in teachers],
            "matrix": matrix,
        },
        ttl_sec=900,
    )
    # Audit preview
    try:
        audit.log(
            "OWNER_ASSIGN_PREVIEW",
            actor.id,
            request_id=req_id,
            meta={
                "strategy": "round_robin",
                "teachers": len(teachers),
                "weeks": len(weeks),
                "students": len(students),
            },
        )
    except Exception:
        pass
    # Render preview summary
    teacher_lines = [
        f"— {t[1] or '(без имени)'} (cap {int(t[2])})" for t in teachers[:10]
    ]
    more_teachers = "\n…" if len(teachers) > 10 else ""
    lines = [
        "📋 Предпросмотр матрицы назначений",
        f"Студентов: {len(students)}; Преподавателей: {len(teachers)}; Недель: {len(weeks)}",
        "Преподаватели:",
        *teacher_lines,
        more_teachers,
        "\nПодтвердить создание матрицы?",
    ]
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✅ Подтвердить", callback_data=_cb_as("c")
                ),
                types.InlineKeyboardButton(text="Отмена", callback_data=cb("people")),
            ],
            _nav_keyboard("people").inline_keyboard[0],
        ]
    )
    banner = await _maybe_banner(uid)
    try:
        await cq.message.edit_text(
            banner + "\n".join([x for x in lines if x]), reply_markup=kb
        )
    except Exception:
        await cq.message.answer(
            banner + "\n".join([x for x in lines if x]), reply_markup=kb
        )
    await cq.answer()


@router.callback_query(_is_as("c"))
async def ownui_people_matrix_commit(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    if _get_impersonation(uid):
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    # one-shot token consume
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    try:
        action, st = state_store.get(_assign_key(uid))
    except Exception:
        action, st = None, None
    if action != "assign_preview" or not st:
        return await cq.answer("Сессия истекла, повторите шаг", show_alert=True)
    matrix = st.get("matrix") or []
    req_id = st.get("req")
    import time

    now = int(time.time())
    # Revalidate snapshot to avoid FK slips

    try:
        with db() as conn:
            weeks_cur = {
                int(r[0]) for r in conn.execute("SELECT week_no FROM weeks").fetchall()
            }
            students_cur = {
                str(r[0])
                for r in conn.execute(
                    "SELECT id FROM users WHERE role='student' AND (is_active IS NULL OR is_active=1)"
                ).fetchall()
            }
            teachers_rows = conn.execute(
                (
                    "SELECT id, COALESCE(capacity,0) FROM users "
                    "WHERE (role='teacher' OR (role='owner' AND tg_id=?)) "
                    "AND (is_active IS NULL OR is_active=1) AND COALESCE(capacity,0) > 0"
                ),
                (actor.tg_id,),
            ).fetchall()
            teachers_cur = {str(r[0]) for r in teachers_rows}
            total_cap = sum(int(r[1]) for r in teachers_rows)
        # Validate references and capacity
        if total_cap < len(st.get("students", [])):
            try:
                audit.log(
                    "OWNER_ASSIGN_COMMIT",
                    actor.id,
                    request_id=req_id,
                    meta={"error": "INSUFFICIENT_CAPACITY"},
                )
            except Exception:
                pass
            return await cq.answer(
                "⛔ Недостаточная суммарная вместимость преподавателей. Пересоздайте превью.",
                show_alert=True,
            )
        for row in matrix:
            if (
                int(row.get("week_no", 0)) not in weeks_cur
                or str(row.get("student_id")) not in students_cur
                or str(row.get("teacher_id")) not in teachers_cur
            ):
                try:
                    audit.log(
                        "OWNER_ASSIGN_COMMIT",
                        actor.id,
                        request_id=req_id,
                        meta={"error": "E_STATE_INVALID_CHANGED"},
                    )
                except Exception:
                    pass
                return await cq.answer(
                    "⛔ Данные изменились. Создайте превью заново.", show_alert=True
                )
    except Exception:
        # Best-effort: if revalidation fails, continue to DB write guarded by FK
        pass
    try:
        with db() as conn:
            conn.execute("BEGIN")
            sql = (
                "INSERT INTO teacher_student_assignments(week_no, teacher_id, student_id, created_at_utc) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(week_no, student_id) DO UPDATE SET teacher_id=excluded.teacher_id, created_at_utc=excluded.created_at_utc"
            )
            for row in matrix:
                conn.execute(
                    sql,
                    (
                        int(row["week_no"]),
                        str(row["teacher_id"]),
                        str(row["student_id"]),
                        now,
                    ),
                )
            conn.commit()
    except Exception as e:
        # audit failure
        try:
            audit.log(
                "OWNER_ASSIGN_COMMIT",
                actor.id,
                request_id=req_id,
                meta={"error": str(e)},
            )
        except Exception:
            pass
        return await cq.answer("⛔ Не удалось применить матрицу", show_alert=True)
    # Audit commit
    try:
        audit.log(
            "OWNER_ASSIGN_COMMIT",
            actor.id,
            request_id=req_id,
            meta={"rows": len(matrix)},
        )
    except Exception:
        pass
    # Cleanup state
    try:
        state_store.delete(_assign_key(uid))
    except Exception:
        pass
    banner = await _maybe_banner(uid)
    await cq.message.answer(
        banner + "✅ Матрица создана", reply_markup=_nav_keyboard("people")
    )
    await cq.answer()


# -------- Reports: assignment matrix export --------


@router.callback_query(_is("own", {"rep_matrix"}))
async def ownui_reports_matrix(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    # gасим токен
    try:
        callbacks.extract(cq.data, expected_role=actor.role)
    except Exception:
        pass
    if not backup_recent():
        return await cq.answer("⛔ Недоступно: нет свежего бэкапа", show_alert=True)
    # Build wide CSV: student, group, Wxx...
    import csv
    import io
    import time as _t

    with db() as conn:
        weeks = [
            r[0]
            for r in conn.execute(
                "SELECT week_no FROM weeks ORDER BY week_no ASC"
            ).fetchall()
        ]
        students = conn.execute(
            (
                "SELECT id, COALESCE(name,''), COALESCE(group_name,'') "
                "FROM users "
                "WHERE role='student' AND (is_active IS NULL OR is_active=1) "
                "ORDER BY COALESCE(group_name,''), COALESCE(name,''), id"
            )
        ).fetchall()
        # Build map (student_id, week_no) -> teacher name (works for owner-as-teacher too)
        rows = conn.execute(
            (
                "SELECT tsa.student_id, tsa.week_no, COALESCE(u.name,'') "
                "FROM teacher_student_assignments tsa "
                "JOIN users u ON u.id = tsa.teacher_id"
            )
        ).fetchall()
    # Explicit error if matrix does not exist (no assignments at all)
    if not rows:
        return await cq.answer("⛔ Матрица назначений не создана", show_alert=True)
    m = {(str(r[0]), int(r[1])): (r[2] or "") for r in rows}
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["student", "group"] + [f"W{int(x):02d}" for x in weeks]
    w.writerow(header)
    for sid, sname, sgroup in students:
        row = [sname or "", sgroup or ""]
        for wk in weeks:
            row.append(m.get((str(sid), int(wk)), ""))
        w.writerow(row)
    data = buf.getvalue().encode("utf-8")
    ts = _t.strftime("%Y%m%d_%H%M%S", _t.gmtime())
    try:
        await cq.message.answer_document(
            types.BufferedInputFile(data, filename=f"assignment_matrix_{ts}.csv"),
            caption="Экспорт assignment matrix (CSV)",
        )
        try:
            audit.log(
                "OWNER_REPORT_EXPORT", actor.id, meta={"type": "assignment_matrix_csv"}
            )
        except Exception:
            pass
    except Exception:
        return await cq.answer(
            "⛔ Не удалось сформировать/отправить экспорт", show_alert=True
        )
    await cq.answer()
