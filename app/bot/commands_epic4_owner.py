from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command

from app.core import callbacks, state_store
from app.core.auth import Identity
from app.core.errors import StateNotFound
from app.core.files import save_blob
from app.core.imports_epic5 import (
    STUDENT_HEADERS,
    TEACHER_HEADERS,
    get_templates,
    get_users_summary,
    import_students_csv,
    import_teachers_csv,
)
from app.core.repos_epic4 import insert_week_material_file, list_weeks

try:
    from aiogram.types import BufferedInputFile  # aiogram v3
except Exception:  # pragma: no cover
    BufferedInputFile = None  # type: ignore

router = Router(name="epic4.owner")


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


def _uid(x: types.Message | types.CallbackQuery) -> int:
    return x.from_user.id


def _amw_key(uid: int) -> str:
    return f"amw:{uid}"


def _safe_get(key: str) -> dict | None:
    try:
        _, params = state_store.get(key)
        return params
    except StateNotFound:
        return None


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
                    "amw", {"action": "week", "params": {"week": n}}, role=role
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
                        "amw",
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
                        "amw",
                        {"action": "page", "params": {"page": page + 1}},
                        role=role,
                    ),
                )
            )
        if nav:
            rows.append(nav)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _visibility_keyboard(role: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Студентам (public)",
                    callback_data=callbacks.build(
                        "amw",
                        {"action": "vis", "params": {"vis": "public"}},
                        role=role,
                    ),
                ),
                types.InlineKeyboardButton(
                    text="Только преподам",
                    callback_data=callbacks.build(
                        "amw",
                        {
                            "action": "vis",
                            "params": {"vis": "teacher_only"},
                        },
                        role=role,
                    ),
                ),
            ]
        ]
    )


def _done_cancel_keyboard(role: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Готово",
                    callback_data=callbacks.build(
                        "amw", {"action": "done", "params": {}}, role=role
                    ),
                ),
                types.InlineKeyboardButton(
                    text="Отмена",
                    callback_data=callbacks.build(
                        "amw", {"action": "cancel", "params": {}}, role=role
                    ),
                ),
            ]
        ]
    )


@router.message(Command("add_material_week"))
async def add_material_week_start(m: types.Message, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await m.answer("Недостаточно прав.")
    await m.answer("Выберите неделю:", reply_markup=_weeks_keyboard(actor.role, page=0))


@router.callback_query(_cb("amw", {"page"}))
async def amw_page(cq: types.CallbackQuery, actor: Identity):
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


@router.callback_query(_cb("amw", {"week"}))
async def amw_pick_week(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    week_no = int(payload["params"].get("week", 0))
    state_store.put_at(
        _amw_key(uid),
        "amw",
        {"mode": "expect_visibility", "week_no": week_no},
        ttl_sec=900,
    )
    await cq.message.answer(
        f"Неделя {week_no}. Выберите видимость:",
        reply_markup=_visibility_keyboard(actor.role),
    )
    await cq.answer()


@router.callback_query(_cb("amw", {"vis"}))
async def amw_set_visibility(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    st = _safe_get(_amw_key(uid))
    if not st or st.get("mode") != "expect_visibility" or not st.get("week_no"):
        await cq.message.answer(
            "Сначала начните с /add_material_week и выберите неделю."
        )
        return await cq.answer()
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    vis = payload["params"].get("vis")
    new_state = {"mode": "expect_files", "week_no": st["week_no"], "visibility": vis}
    state_store.put_at(_amw_key(uid), "amw", new_state, ttl_sec=900)
    await cq.message.answer(
        "Окей. Теперь пришлите один или несколько документов для этой недели. Когда закончите — нажмите «Готово».",
        reply_markup=_done_cancel_keyboard(actor.role),
    )
    await cq.answer()


async def _has_amw_files_state(m: types.Message) -> bool:
    st = _safe_get(_amw_key(m.from_user.id))
    if not st:
        return False
    mode_ok = st.get("mode") == "expect_files"
    vis_ok = st.get("visibility") in ("public", "teacher_only")
    return bool(mode_ok and vis_ok)


@router.message(F.document, _has_amw_files_state)
async def amw_receive_file(m: types.Message, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return
    uid = _uid(m)
    st = _safe_get(_amw_key(uid))
    if not st:
        return await m.answer("Сначала начните с /add_material_week и выберите неделю.")
    mode_ok = st.get("mode") == "expect_files"
    vis_ok = st.get("visibility") in ("public", "teacher_only")
    if not (mode_ok and vis_ok):
        return await m.answer("Сначала начните с /add_material_week и выберите неделю.")

    doc = m.document
    file = await m.bot.get_file(doc.file_id)
    b = await m.bot.download_file(file.file_path)
    data = b.read()

    saved = save_blob(
        data, prefix="materials", suggested_name=doc.file_name or "material.bin"
    )
    mid = insert_week_material_file(
        week_no=st["week_no"],
        uploaded_by=actor.id,
        path=saved.path,
        sha256=saved.sha256,
        size_bytes=saved.size_bytes,
        mime=doc.mime_type,
        visibility=st["visibility"],
        type="p",
        original_name=doc.file_name or None,
    )
    # продлеваем TTL после действия
    state_store.put_at(_amw_key(uid), "amw", st, ttl_sec=900)
    if mid == -1:
        await m.answer(
            "⚠️ Такой материал уже загружен ранее (тот же файл).",
            reply_markup=_done_cancel_keyboard(actor.role),
        )
    else:
        await m.answer(
            f"✅ Материал #{mid} добавлен ({st['visibility']}). Ещё файл? Или «Готово».",
            reply_markup=_done_cancel_keyboard(actor.role),
        )


@router.callback_query(_cb("amw", {"done"}))
async def amw_done(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    callbacks.extract(cq.data, expected_role=actor.role)
    state_store.delete(_amw_key(uid))
    await cq.message.answer("Готово. Добавление материалов завершено.")
    await cq.answer()


@router.callback_query(_cb("amw", {"cancel"}))
async def amw_cancel(cq: types.CallbackQuery, actor: Identity):
    if not _is_owner_or_teacher(actor):
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    callbacks.extract(cq.data, expected_role=actor.role)
    state_store.delete(_amw_key(uid))
    await cq.message.answer("Ок, отменил. Ничего не сохранил.")
    await cq.answer()


@router.message(Command("cancel"))
async def owner_cancel_cmd(m: types.Message):
    uid = _uid(m)
    # Чистим оба потенциальных ключа на всякий случай
    try:
        state_store.delete(f"amw:{uid}")
    except Exception:
        pass
    try:
        state_store.delete(f"wk_submit:{uid}")
    except Exception:
        pass
    await m.answer("Ок, отменил. Активные сценарии сброшены.")


# ========= EPIC-5: Owner Import Menu =========


def _import_menu_keyboard(role: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Импорт преподавателей",
                    callback_data=callbacks.build(
                        "imp", {"action": "teachers", "params": {}}, role=role
                    ),
                ),
                types.InlineKeyboardButton(
                    text="Импорт студентов",
                    callback_data=callbacks.build(
                        "imp", {"action": "students", "params": {}}, role=role
                    ),
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="Скачать шаблоны",
                    callback_data=callbacks.build(
                        "imp", {"action": "templates", "params": {}}, role=role
                    ),
                ),
                types.InlineKeyboardButton(
                    text="Сводка",
                    callback_data=callbacks.build(
                        "imp", {"action": "summary", "params": {}}, role=role
                    ),
                ),
            ],
        ]
    )


def _imp_key(uid: int) -> str:
    return f"imp:{uid}"


@router.message(Command("import_data"))
async def import_data_menu(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return await m.answer("Недостаточно прав.")
    await m.answer("📥 Импорт данных:", reply_markup=_import_menu_keyboard(actor.role))


@router.callback_query(_cb("imp", {"teachers", "students"}))
async def imp_select_mode(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    uid = _uid(cq)
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    mode = payload.get("action")
    state_store.put_at(_imp_key(uid), "imp", {"mode": mode}, ttl_sec=900)
    if mode == "teachers":
        headers = ",".join(TEACHER_HEADERS)
        await cq.message.answer(
            "Отправьте CSV (преподаватели) с заголовками:\n" + headers
        )
    else:
        headers = ",".join(STUDENT_HEADERS)
        await cq.message.answer("Отправьте CSV (студенты) с заголовками:\n" + headers)
    await cq.answer()


def _has_imp_state(m: types.Message) -> bool:
    st = _safe_get(_imp_key(m.from_user.id))
    return bool(st and st.get("mode") in ("teachers", "students"))


@router.message(F.document, _has_imp_state)
async def imp_receive_csv(m: types.Message, actor: Identity):
    if actor.role != "owner":
        return
    uid = _uid(m)
    st = _safe_get(_imp_key(uid)) or {}
    mode = st.get("mode")
    if mode not in ("teachers", "students"):
        return
    doc = m.document
    file = await m.bot.get_file(doc.file_id)
    b = await m.bot.download_file(file.file_path)
    content = b.read()

    if mode == "teachers":
        res = import_teachers_csv(content)
        title = "Импорт преподавателей"
        err_name = "import_errors_teachers.csv"
    else:
        res = import_students_csv(content)
        title = "Импорт студентов"
        err_name = "import_errors_students.csv"

    lines = [
        f"{title}:",
        f"• создано: {res.created}",
        f"• обновлено: {res.updated}",
        f"• ошибок: {len(res.errors)}",
    ]

    state_store.delete(_imp_key(uid))

    if res.errors and BufferedInputFile is not None:
        await m.answer("\n".join(lines))
        await m.answer_document(
            BufferedInputFile(res.to_error_csv(), filename=err_name),
            caption="Отчёт об ошибках",
        )
    else:
        await m.answer("\n".join(lines))


@router.callback_query(_cb("imp", {"templates"}))
async def imp_templates(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    callbacks.extract(cq.data, expected_role=actor.role)
    tpls = get_templates()
    if BufferedInputFile is not None:
        await cq.message.answer_document(
            BufferedInputFile(tpls["teachers.csv"], filename="teachers.csv"),
            caption="Шаблон: преподаватели",
        )
        await cq.message.answer_document(
            BufferedInputFile(tpls["students.csv"], filename="students.csv"),
            caption="Шаблон: студенты",
        )
    else:
        await cq.message.answer("Не удалось подготовить файлы.")
    await cq.answer()


@router.callback_query(_cb("imp", {"summary"}))
async def imp_summary(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "owner":
        return await cq.answer("Нет прав", show_alert=True)
    callbacks.extract(cq.data, expected_role=actor.role)
    s = get_users_summary()
    lines = [
        "Сводка пользователей:",
        f"– Teachers: всего {s['teachers_total']}, без tg_id {s['teachers_no_tg']}",
        f"– Students: всего {s['students_total']}, без tg_id {s['students_no_tg']}",
    ]
    await cq.message.answer("\n".join(lines))
    await cq.answer()
