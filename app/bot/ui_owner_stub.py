from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart

from app.core import callbacks, state_store
from app.core.auth import Identity, get_user_by_tg
from app.core.backup import backup_recent
from app.core.course_init import apply_course_init, parse_weeks_csv
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
    weeks_btn = types.InlineKeyboardButton(
        text="Загрузка недель (CSV)",
        callback_data=cb("course_weeks_csv"),
    )
    rows = [
        [init_btn],
        [info_btn],
        [weeks_btn],
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


@router.callback_query(_is("own", {"course_info", "course_weeks_csv"}))
async def ownui_course_stub(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    banner = await _maybe_banner(_uid(cq))
    await cq.message.answer(
        banner + "⛔ Функция не реализована", reply_markup=_nav_keyboard("course")
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
        if any(
            e.startswith("E_FORMAT_COLUMNS") or ":E_FORMAT_COLUMNS" in e
            for e in parsed.errors
        ):
            await m.answer("⛔ Ошибка формата CSV (лишние/неверные колонки)")
        elif any(":E_DEADLINE_INVALID" in e for e in parsed.errors):
            await m.answer("⛔ Некорректная дата дедлайна")
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
    preview_lines = [
        "Предпросмотр недель:",
    ]
    for r in rows[:10]:
        wn = r.get("week_no")
        tp = r.get("topic") or ""
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
                text="Импорт студентов (CSV)",
                callback_data=cb("people_imp_students"),
            ),
            types.InlineKeyboardButton(
                text="Импорт преподавателей (CSV)",
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
                callback_data=cb("people_matrix"),
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
            "people_imp_students",
            "people_imp_teachers",
            "people_search",
            "people_matrix",
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


# -------- Materials --------


def _materials_weeks_kb(page: int = 0) -> types.InlineKeyboardMarkup:
    # 28 per page, 7 columns
    per_page = 28
    total = 56  # stubbed count of weeks
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page + 1
    end = min(total, start + per_page - 1)
    rows: list[list[types.InlineKeyboardButton]] = []
    row: list[types.InlineKeyboardButton] = []
    for n in range(start, end + 1):
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
    await cq.message.answer(
        banner + "Карточка материала:",
        reply_markup=_material_card_kb(week, t, impersonating=bool(imp)),
    )
    await cq.answer()


@router.callback_query(
    _is(
        "own",
        {"mat_upload", "mat_download", "mat_history", "mat_archive", "mat_delete"},
    )
)
async def ownui_material_card_stubs(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    act = payload.get("action")
    imp = _get_impersonation(_uid(cq))
    if imp and act in {"mat_upload", "mat_archive", "mat_delete"}:
        return await cq.answer(
            "⛔ Действие недоступно в режиме имперсонизации", show_alert=True
        )
    await cq.answer("⛔ Функция не реализована", show_alert=True)


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
        banner + f"Архив W{week}: версии (заглушка)",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cq.answer()
    _stack_push(_uid(cq), "arch_materials_versions", {"week": week})


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


@router.callback_query(
    _is("own", {"rep_audit", "rep_grades", "rep_matrix", "rep_course"})
)
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
    if not backup_recent():
        return await cq.answer("⛔ Недоступно: нет свежего бэкапа", show_alert=True)
    await cq.answer("✅ Бэкап запущен", show_alert=True)


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
