from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command

from app.core import state_store
from app.core.auth import Identity
from app.core.errors import StateNotFound
from app.core.files import save_blob
from app.core.repos_epic4 import (
    add_submission_file,
    get_or_create_week_submission,
    list_materials_by_week,
    list_student_weeks,
    list_submission_files,
    list_weeks,
    soft_delete_submission_file,
)

router = Router(name="epic4.student")


def _uid(msg_or_cq: types.Message | types.CallbackQuery) -> int:
    return (
        msg_or_cq.from_user.id
        if isinstance(msg_or_cq, types.CallbackQuery)
        else msg_or_cq.from_user.id
    )


def _wk_key(uid: int) -> str:
    return f"wk_submit:{uid}"


def _safe_get(key: str):
    try:
        return state_store.get(key)
    except StateNotFound:
        return None


def _weeks_keyboard(prefix: str, page: int = 0) -> types.InlineKeyboardMarkup:
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
            types.InlineKeyboardButton(text=f"W{n}", callback_data=f"{prefix}:week:{n}")
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
                    text="« Назад", callback_data=f"{prefix}:page:{page - 1}"
                )
            )
        if page < total_pages - 1:
            nav.append(
                types.InlineKeyboardButton(
                    text="Вперёд »", callback_data=f"{prefix}:page:{page + 1}"
                )
            )
        if nav:
            rows.append(nav)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _files_list_markup(files: list[dict]) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    row: list[types.InlineKeyboardButton] = []
    for f in files:
        row.append(
            types.InlineKeyboardButton(
                text=f"Удалить #{f['id']}", callback_data=f"subw:del:{f['id']}"
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    # меню под списком
    rows.append(
        [
            types.InlineKeyboardButton(text="Готово", callback_data="subw:done"),
            types.InlineKeyboardButton(text="Отмена", callback_data="subw:cancel"),
            types.InlineKeyboardButton(text="Мои файлы", callback_data="subw:list"),
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _submit_menu_markup() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="Готово", callback_data="subw:done"),
                types.InlineKeyboardButton(text="Отмена", callback_data="subw:cancel"),
                types.InlineKeyboardButton(text="Мои файлы", callback_data="subw:list"),
            ]
        ]
    )


# ---------- (A) MATERIALS ----------


@router.message(Command("materials"))
async def materials_start(m: types.Message):
    await m.answer("Выберите неделю:", reply_markup=_weeks_keyboard("mat", page=0))


@router.callback_query(F.data.regexp(r"^mat:page:(\d+)$"))
async def materials_page(cq: types.CallbackQuery):
    page = int(cq.data.split(":")[2])
    try:
        await cq.message.edit_reply_markup(
            reply_markup=_weeks_keyboard("mat", page=page)
        )
    except Exception:
        await cq.message.answer(
            "Выберите неделю:", reply_markup=_weeks_keyboard("mat", page=page)
        )
    await cq.answer()


@router.callback_query(F.data.regexp(r"^mat:week:(\d+)$"))
async def materials_week(cq: types.CallbackQuery, actor: Identity):
    week_no = int(cq.data.split(":")[2])
    audience = "teacher" if actor.role in ("owner", "teacher") else "student"
    mats = list_materials_by_week(week_no, audience=audience)
    if not mats:
        await cq.message.answer("Для этой недели нет материалов.")
        await cq.answer()
        return
    lines = []
    for m in mats:
        short = m.sha256[:8] if m.sha256 else m.path
        lines.append(f"• #{m.id} — {m.mime or 'file'} | size={m.size_bytes} | {short}")
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="← К неделям", callback_data="mat:back")]
        ]
    )
    await cq.message.answer("\n".join(lines[:50]), reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data == "mat:back")
async def materials_back(cq: types.CallbackQuery):
    try:
        await cq.message.edit_reply_markup(reply_markup=_weeks_keyboard("mat", page=0))
    except Exception:
        await cq.message.answer(
            "Выберите неделю:", reply_markup=_weeks_keyboard("mat", page=0)
        )
    await cq.answer()


# ---------- (B) SUBMIT WEEK ----------


@router.message(Command("submit_week"))
async def submit_week_start(m: types.Message, actor: Identity):
    if actor.role != "student":
        return await m.answer("Эта команда доступна только студентам.")
    await m.answer("Выберите неделю:", reply_markup=_weeks_keyboard("subw", page=0))


@router.callback_query(F.data.regexp(r"^subw:page:(\d+)$"))
async def submit_week_page(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("Только для студентов", show_alert=True)
    page = int(cq.data.split(":")[2])
    try:
        await cq.message.edit_reply_markup(
            reply_markup=_weeks_keyboard("subw", page=page)
        )
    except Exception:
        await cq.message.answer(
            "Выберите неделю:", reply_markup=_weeks_keyboard("subw", page=page)
        )
    await cq.answer()


@router.callback_query(F.data.regexp(r"^subw:week:(\d+)$"))
async def submit_week_pick(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer(
            "Эта команда доступна только студентам.", show_alert=True
        )
    week_no = int(cq.data.split(":")[2])
    uid = _uid(cq)
    sub_id = get_or_create_week_submission(actor.id, week_no)
    state_store.put_at(
        _wk_key(uid),
        {"mode": "collecting", "week_no": week_no, "sub_id": sub_id},
        ttl_sec=900,
    )
    await cq.message.answer(
        f"Неделя {week_no}. Отправьте один или несколько документов. Когда закончите — «Готово».",
        reply_markup=_submit_menu_markup(),
    )
    await cq.answer()


@router.message(F.document)
async def submit_receive_file(m: types.Message, actor: Identity):
    """
    Принимает файлы студента ТОЛЬКО если есть активная сессия /submit_week (mode='collecting').
    Иначе — молча выходим (не мешаем owner-хендлерам).
    """
    st = _safe_get(_wk_key(_uid(m)))
    if not st or st.get("mode") != "collecting":
        # Вне сценария сдачи — ничего не отвечаем.
        return

    doc = m.document
    # (опционально) валидации размера/mime:
    # if doc.file_size and doc.file_size > 25*1024*1024:
    #     return await m.answer("Файл слишком большой (лимит 25 МБ).")

    # Скачиваем один раз, сохраняем один раз
    tg_file = await m.bot.get_file(doc.file_id)
    stream = await m.bot.download_file(tg_file.file_path)
    data = stream.read()

    saved = save_blob(
        data,
        prefix="submissions",
        suggested_name=doc.file_name or "submission.bin",  # сохраняем расширение
    )

    file_id = add_submission_file(
        submission_id=st["sub_id"],
        sha256=saved.sha256,
        size_bytes=saved.size_bytes,
        path=saved.path,
        mime=doc.mime_type,
    )

    if file_id == -1:
        await m.answer("⚠️ Такой файл уже есть в вашей сдаче (тот же хэш и размер).")
    else:
        await m.answer(
            f"✅ Файл добавлен (id={file_id}, size={saved.size_bytes}). Ещё файлы? Или нажмите «Готово»."
        )


@router.callback_query(F.data == "subw:list")
async def submit_list(cq: types.CallbackQuery, actor: Identity):
    st = _safe_get(_wk_key(_uid(cq)))
    if not st or st.get("mode") != "collecting":
        await cq.message.answer("Сначала вызовите /submit_week и выберите неделю.")
        return await cq.answer()
    files = list_submission_files(actor.id, st["week_no"])
    if not files:
        await cq.message.answer("Пока файлов нет.", reply_markup=_submit_menu_markup())
        return await cq.answer()
    lines = [
        f"• #{f['id']} — size={f['size_bytes']} | {f.get('mime') or 'file'}"
        for f in files
    ]
    await cq.message.answer(
        "\n".join(lines[:50]), reply_markup=_files_list_markup(files)
    )
    await cq.answer()


@router.callback_query(F.data.regexp(r"^subw:del:(\d+)$"))
async def submit_delete(cq: types.CallbackQuery, actor: Identity):
    st = _safe_get(_wk_key(_uid(cq)))
    if not st or st.get("mode") != "collecting":
        await cq.message.answer("Сначала вызовите /submit_week и выберите неделю.")
        return await cq.answer()
    fid = int(cq.data.split(":")[2])
    ok = soft_delete_submission_file(fid, actor.id)
    await cq.message.answer("✅ Удалил." if ok else "Не найден/не ваш.")
    # обновим список
    files = list_submission_files(actor.id, st["week_no"])
    lines = [
        f"• #{f['id']} — size={f['size_bytes']} | {f.get('mime') or 'file'}"
        for f in files
    ]
    await cq.message.answer(
        "\n".join(lines) if lines else "Пока файлов нет.",
        reply_markup=_files_list_markup(files),
    )
    await cq.answer()


@router.callback_query(F.data == "subw:done")
async def submit_done(cq: types.CallbackQuery):
    uid = _uid(cq)
    st = _safe_get(_wk_key(uid))
    if not st:
        await cq.message.answer("Активной сессии отправки нет.")
        return await cq.answer()
    state_store.delete(_wk_key(uid))
    await cq.message.answer(
        f"Готово. Сдача недели {st['week_no']} сохранена со статусом 'submitted'."
    )
    await cq.answer()


@router.callback_query(F.data == "subw:cancel")
async def submit_cancel(cq: types.CallbackQuery):
    uid = _uid(cq)
    state_store.delete(_wk_key(uid))
    await cq.message.answer("Ок, отменил. Ничего не сохранил.")
    await cq.answer()


# ---------- (C) MY SUBMISSIONS ----------


@router.message(Command("my_submissions"))
async def my_submissions(m: types.Message, actor: Identity):
    if actor.role != "student":
        return await m.answer("Доступно только для студентов.")
    weeks = list_student_weeks(actor.id, limit=20)
    if not weeks:
        return await m.answer("Пока нет сдач.")
    lines = [f"• week {w} — файлов: {cnt}" for (w, cnt) in weeks]
    await m.answer("\n".join(lines))


# /cancel — общий хендлер реализован в owner/teacher части для единообразия
