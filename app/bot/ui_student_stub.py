from __future__ import annotations

import html

from aiogram import F, Router, types
from aiogram.filters import Command

from app.core import audit, callbacks, state_store
from app.core.auth import Identity
from app.core.files import ensure_parent_dir, link_or_copy, safe_filename, save_blob
from app.core.repos_epic4 import (
    add_student_submission_file,
    list_materials_by_week,
    list_submission_files,
    list_weeks_with_titles,
    soft_delete_student_submission_file,
)
from app.db.conn import db
from app.services.common.time_service import format_datetime, get_course_tz, utc_now_ts

router = Router(name="ui.student.stub")

try:
    from aiogram.types import BufferedInputFile
except Exception:  # pragma: no cover
    BufferedInputFile = None  # type: ignore


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


def _weeks_kb(page: int = 0, per_page: int = 8) -> types.InlineKeyboardMarkup:
    items = list_weeks_with_titles(limit=200)
    weeks = sorted(items, key=lambda x: x[0])
    total_pages = max(1, (len(weeks) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = weeks[start : start + per_page]
    rows: list[list[types.InlineKeyboardButton]] = []
    for wno, title in chunk:
        label = f"📘 Неделя {int(wno)}"
        if title:
            label += f". {title}"
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
                text="📄 Материалы недели",
                callback_data=cb("materials_week", {"week": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📚 Конспект", callback_data=cb("week_notes", {"week": week})
            ),
            types.InlineKeyboardButton(
                text="📊 Презентации", callback_data=cb("week_slides", {"week": week})
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="🎥 Запись лекции", callback_data=cb("week_video", {"week": week})
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


# ------- Upload solutions (student) -------


def _upload_key(uid: int) -> str:
    return f"s_upload:{uid}"


def _fmt_bytes(num: int) -> str:
    try:
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if num < 1024:
                return f"{num} {unit}"
            num //= 1024
    except Exception:
        pass
    return f"{num} Б"


def _allowed_submission_exts() -> set[str]:
    # Только картинки или PDF
    return {".pdf", ".png", ".jpg", ".jpeg"}


def _student_bucket(actor: Identity) -> str:
    # В доке — humanized ID (ST001). В текущей схеме его нет, используем UUID студента.
    return str(actor.id or "unknown")


def _surname_from_name(full_name: str | None) -> str:
    try:
        if not full_name:
            return "student"
        parts = [p for p in full_name.strip().split() if p]
        if not parts:
            return "student"
        cand = parts[0]
        return safe_filename(cand)
    except Exception:
        return "student"


def _next_file_index(student_id: str, week: int) -> int:
    try:
        with db() as conn:
            row = conn.execute(
                (
                    "SELECT COUNT(1) FROM students_submissions "
                    "WHERE student_id=? AND week_no=? AND deleted_at_utc IS NULL"
                ),
                (student_id, week),
            ).fetchone()
            return int(row[0] or 0) + 1
    except Exception:
        return 1


def _materialize_submission_path(actor: Identity, week: int, *, ext: str) -> str:
    import os

    safe_ext = (ext or "").lower()
    if not safe_ext.startswith("."):
        safe_ext = "." + safe_ext if safe_ext else ".bin"
    surname = _surname_from_name(actor.name)
    week_tag = f"Н{int(week):02d}"
    index = _next_file_index(actor.id, week)
    fname = f"{surname}_{week_tag}_{index}{safe_ext}"
    rel = os.path.join(
        "var", "submissions", _student_bucket(actor), f"W{int(week):02d}", fname
    )
    ensure_parent_dir(rel)
    return rel


def _week_upload_kb(
    week: int, last_file_id: int | None = None
) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    rows.append(
        [
            types.InlineKeyboardButton(
                text="➕ Загрузить ещё", callback_data=cb("week_upload", {"week": week})
            )
        ]
    )
    if last_file_id is not None:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🗑️ Удалить этот файл",
                    callback_data=cb(
                        "week_upload_delete", {"week": week, "fid": int(last_file_id)}
                    ),
                )
            ]
        )
    rows.append(_nav_keyboard().inline_keyboard[0])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is({"week_upload"}))
async def sui_week_upload(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
        payload = {}
    week_no = int((payload or {}).get("week", 0))
    if not week_no:
        return await _toast_error(cq, "E_INPUT_INVALID")
    # Put state to expect a document
    state_store.put_at(
        _upload_key(_uid(cq)),
        "s_upload",
        {"mode": "await_doc", "w": week_no},
        ttl_sec=900,
    )
    # Show instruction + current counters
    try:
        files = list_submission_files(actor.id, week_no)
    except Exception:
        files = []
    total_sz = sum(int(f.get("size_bytes") or 0) for f in files)
    title_map = dict(list_weeks_with_titles(limit=200))
    title = title_map.get(int(week_no), "")
    header = (
        f"📤 <b>Загрузка решений — W{int(week_no):02d}"
        + (f". {html.escape(title)}" if title else "")
        + "</b>"
    )
    lines = [
        header,
        "Отправьте файл (PNG/JPG/PDF)",
        "Лимиты: ≤5 файлов, ≤30 МБ суммарно",
        f"Сейчас: файлов {len(files)}, сумма {_fmt_bytes(total_sz)}",
    ]
    kb = _week_upload_kb(week_no)
    try:
        await cq.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "week_upload", {"week": int(week_no)})


def _awaits_upload_doc(m: types.Message) -> bool:
    try:
        act, st = state_store.get(_upload_key(_uid(m)))
        return act == "s_upload" and (st or {}).get("mode") == "await_doc"
    except Exception:
        return False


@router.message(F.document, _awaits_upload_doc)
async def sui_receive_submission_doc(m: types.Message, actor: Identity):
    if actor.role != "student":
        return
    try:
        _, st = state_store.get(_upload_key(_uid(m)))
    except Exception:
        return
    week = int(st.get("w", 0))
    if not week:
        return
    doc = m.document
    # Validate limits before download
    try:
        current_files = list_submission_files(actor.id, week)
    except Exception:
        current_files = []
    if len(current_files) >= 5:
        return await m.answer("⚠️ Достигнут лимит: ≤5 файлов")
    try:
        fsz = int(getattr(doc, "file_size", 0) or 0)
    except Exception:
        fsz = 0
    total_sz = sum(int(f.get("size_bytes") or 0) for f in current_files)
    if fsz and total_sz + fsz > 30 * 1024 * 1024:
        return await m.answer("⚠️ Превышен лимит: ≤30 МБ суммарно")
    # Validate extension by whitelist
    fname_l = (getattr(doc, "file_name", None) or "").lower()
    ext = "." + fname_l.rsplit(".", 1)[-1] if "." in fname_l else ""
    if ext not in _allowed_submission_exts():
        return await m.answer("⛔ Неподдерживаемый тип файла")
    # Download and save
    try:
        file = await m.bot.get_file(doc.file_id)
        b = await m.bot.download_file(file.file_path)
        data = b.read()
    except Exception:
        return await m.answer("⛔ Ошибка хранения файла")
    saved = save_blob(
        data, prefix="submissions", suggested_name=getattr(doc, "file_name", None)
    )
    # Сначала записываем запись (анти-дубликат), затем материализуем файл по человекочитаемому пути
    dest_path = _materialize_submission_path(actor, week, ext=ext or ".bin")
    fid = add_student_submission_file(
        student_id=actor.id,
        week_no=week,
        sha256=saved.sha256,
        size_bytes=saved.size_bytes,
        path=dest_path,
        mime=getattr(doc, "mime_type", None),
    )
    if fid == -1:
        return await m.answer("⚠️ Такой файл уже загружен (дубликат)")
    try:
        link_or_copy(saved.path, dest_path)
    except Exception:
        pass
    # Audit
    try:
        audit.log(
            "STUDENT_SUBMISSION_UPLOAD",
            actor.id,
            meta={
                "week": int(week),
                "file_uuid": saved.sha256,
                "size_bytes": int(saved.size_bytes),
                "sha256": saved.sha256,
                "storage_path": saved.path,
            },
        )
    except Exception:
        pass
    title_map = dict(list_weeks_with_titles(limit=200))
    title = title_map.get(int(week), "")
    msg = (
        f"📤 <b>Файл загружен — W{int(week):02d}"
        + (f". {html.escape(title)}" if title else "")
        + "</b>"
    )
    await m.answer(
        msg,
        reply_markup=_week_upload_kb(
            week, last_file_id=int(fid) if isinstance(fid, int) else None
        ),
        parse_mode="HTML",
    )
    # keep awaiting for more uploads
    state_store.put_at(
        _upload_key(_uid(m)), "s_upload", {"mode": "await_doc", "w": week}, ttl_sec=900
    )


if hasattr(F, "photo"):

    @router.message(F.photo, _awaits_upload_doc)
    async def sui_receive_submission_photo(m: types.Message, actor: Identity):
        """Handle images sent as photo (Telegram compresses them). Treat as JPG."""
        if actor.role != "student":
            return
        try:
            _, st = state_store.get(_upload_key(_uid(m)))
        except Exception:
            return
        week = int(st.get("w", 0))
        if not week:
            return
        try:
            current_files = list_submission_files(actor.id, week)
        except Exception:
            current_files = []
        if len(current_files) >= 5:
            return await m.answer("⚠️ Достигнут лимит: ≤5 файлов")
        try:
            ph = m.photo[-1] if getattr(m, "photo", None) else None
            fsz = int(getattr(ph, "file_size", 0) or 0)
        except Exception:
            fsz = 0
        total_sz = sum(int(f.get("size_bytes") or 0) for f in current_files)
        if fsz and total_sz + fsz > 30 * 1024 * 1024:
            return await m.answer("⚠️ Превышен лимит: ≤30 МБ суммарно")
        # Download the largest available photo
        try:
            file = await m.bot.get_file(ph.file_id)
            b = await m.bot.download_file(file.file_path)
            data = b.read()
        except Exception:
            return await m.answer("⛔ Ошибка хранения файла")
        # Treat as JPEG by default
        saved = save_blob(data, prefix="submissions", suggested_name="photo.jpg")
        dest_path = _materialize_submission_path(actor, week, ext=".jpg")
        fid = add_student_submission_file(
            student_id=actor.id,
            week_no=week,
            sha256=saved.sha256,
            size_bytes=saved.size_bytes,
            path=dest_path,
            mime="image/jpeg",
        )
        if fid == -1:
            return await m.answer("⚠️ Такой файл уже загружен (дубликат)")
        try:
            link_or_copy(saved.path, dest_path)
        except Exception:
            pass
        try:
            audit.log(
                "STUDENT_SUBMISSION_UPLOAD",
                actor.id,
                meta={
                    "week": int(week),
                    "file_uuid": saved.sha256,
                    "size_bytes": int(saved.size_bytes),
                    "sha256": saved.sha256,
                    "storage_path": saved.path,
                },
            )
        except Exception:
            pass
        title_map = dict(list_weeks_with_titles(limit=200))
        title = title_map.get(int(week), "")
        msg = (
            f"📤 <b>Файл загружен — W{int(week):02d}"
            + (f". {html.escape(title)}" if title else "")
            + "</b>"
        )
        await m.answer(
            msg,
            reply_markup=_week_upload_kb(
                week, last_file_id=int(fid) if isinstance(fid, int) else None
            ),
            parse_mode="HTML",
        )
        # keep awaiting for more uploads
        state_store.put_at(
            _upload_key(_uid(m)),
            "s_upload",
            {"mode": "await_doc", "w": week},
            ttl_sec=900,
        )

else:

    async def sui_receive_submission_photo(*_a, **_k):  # type: ignore
        return None


@router.callback_query(_is({"week_upload_delete"}))
async def sui_delete_submission_file(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
    except Exception:
        return await _toast_error(cq, "E_STATE_EXPIRED")
    week = int(payload.get("week", 0))
    fid = int(payload.get("fid", 0))
    if not week or not fid:
        return await _toast_error(cq, "E_STATE_INVALID")
    ok = soft_delete_student_submission_file(fid, actor.id)
    if not ok:
        return await _toast_error(cq, "E_ACCESS_DENIED", "Не удалось удалить файл")
    # Show updated counters
    try:
        files = list_submission_files(actor.id, week)
    except Exception:
        files = []
    total_sz = sum(int(f.get("size_bytes") or 0) for f in files)
    title_map = dict(list_weeks_with_titles(limit=200))
    title = title_map.get(int(week), "")
    header = (
        f"📤 <b>Загрузка решений — W{int(week):02d}"
        + (f". {html.escape(title)}" if title else "")
        + "</b>"
    )
    lines = [
        header,
        "✅ Файл удалён",
        "Отправьте файл (PNG/JPG/PDF)",
        "Лимиты: ≤5 файлов, ≤30 МБ суммарно",
        f"Сейчас: файлов {len(files)}, сумма {_fmt_bytes(total_sz)}",
    ]
    kb = _week_upload_kb(week)
    try:
        await cq.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    await cq.answer()


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


# ------- Materials for week (student) -------


def _week_id_by_no(week_no: int) -> int | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM weeks WHERE week_no=?", (week_no,)
        ).fetchone()
        return int(row[0]) if row else None


async def _send_material(cq: types.CallbackQuery, *, week_no: int, mtype: str) -> None:
    # Enforce visibility: students see only public materials
    mats = list_materials_by_week(int(week_no), audience="student")
    mat = next(
        (m for m in mats if str(m.type) == mtype and int(m.is_active or 0) == 1), None
    )
    if not mat:
        return await _toast_error(cq, "E_NOT_FOUND", "Материал не найден")
    # Build labels like teacher UI
    title_map = dict(list_weeks_with_titles(limit=200))
    title = title_map.get(int(week_no), "")
    labels = {
        "p": ("📄", "Материалы недели"),
        "n": ("📚", "Конспект"),
        "s": ("📊", "Презентация"),
        "v": ("🎥", "Запись лекции"),
    }
    emoji, name = labels.get(mtype, ("📄", "Материал"))

    # Video/link material
    if mtype == "v":
        url = str(mat.path)
        try:
            msg = f"{emoji} <b>Неделя {int(week_no)}"
            if title:
                msg += f". {title}"
            msg += f'.</b> <a href="{url}">{name}</a>'
            await cq.message.answer(
                msg, parse_mode="HTML", disable_web_page_preview=True
            )
        except Exception:
            pass
        return await cq.answer("Ссылка отправлена")

    # File material
    try:
        import os

        fname = os.path.basename(str(mat.path)) or "material.bin"
        with open(str(mat.path), "rb") as f:
            data = f.read()
        if BufferedInputFile is not None:
            caption = f"{emoji} <b>Неделя {int(week_no)}"
            if title:
                caption += f". {title}"
            caption += f".</b> {name}."
            await cq.message.answer_document(
                BufferedInputFile(data, filename=fname),
                caption=caption,
                parse_mode="HTML",
            )
        else:  # Fallback: send as text path
            await cq.message.answer(f"Файл: {fname}\nПуть: {mat.path}")
        await cq.answer("Файл отправлен")
    except FileNotFoundError:
        await _toast_error(cq, "E_NOT_FOUND", "Файл недоступен")
    except Exception:
        await _toast_error(cq, "E_STATE_INVALID", "Не удалось отправить файл")


def _materials_types_kb_s(week: int) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="📄 Материалы недели",
                callback_data=cb("week_prep", {"week": week}),
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📚 Конспект", callback_data=cb("week_notes", {"week": week})
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📊 Презентация", callback_data=cb("week_slides", {"week": week})
            )
        ],
        [
            types.InlineKeyboardButton(
                text="🎥 Запись лекции", callback_data=cb("week_video", {"week": week})
            )
        ],
        _nav_keyboard().inline_keyboard[0],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_is({"materials_week"}))
async def sui_materials_week(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    week_no = 0
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
        week_no = int(payload.get("week", 0))
    except Exception:
        # Try last week from stack
        last = _stack_pop(_uid(cq)) or {}
        p = dict(last.get("p") or {})
        week_no = int(p.get("week", 0))
    title = dict(list_weeks_with_titles(limit=200)).get(int(week_no), "")
    if title:
        text = f"📚 <b>Неделя {int(week_no)}. {title}</b>\nВыберите материал:"
    else:
        text = f"📚 <b>Неделя {int(week_no)}</b>\nВыберите материал:"
    kb = _materials_types_kb_s(int(week_no))
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cq.answer()
    _stack_push(_uid(cq), "materials_week", {"week": int(week_no)})


@router.callback_query(_is({"week_prep"}))
async def sui_week_send_prep(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
        return
    await _send_material(cq, week_no=int(payload.get("week", 0)), mtype="p")


@router.callback_query(_is({"week_notes"}))
async def sui_week_send_notes(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
        return
    await _send_material(cq, week_no=int(payload.get("week", 0)), mtype="n")


@router.callback_query(_is({"week_slides"}))
async def sui_week_send_slides(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
        return
    await _send_material(cq, week_no=int(payload.get("week", 0)), mtype="s")


@router.callback_query(_is({"week_video"}))
async def sui_week_send_video(cq: types.CallbackQuery, actor: Identity):
    if actor.role != "student":
        return await cq.answer("⛔ Доступ запрещён", show_alert=True)
    try:
        _, payload = callbacks.extract(cq.data, expected_role="student")
    except Exception:
        await _toast_error(cq, "E_STATE_EXPIRED")
        return
    await _send_material(cq, week_no=int(payload.get("week", 0)), mtype="v")


# ------- Stubs for remaining actions -------


def _dev_stub_text() -> str:
    return "Функция в разработке"


@router.callback_query(_is({"week_upload", "week_book", "week_unbook", "week_grade"}))
async def sui_other_week_action_stub(cq: types.CallbackQuery, actor: Identity):
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


# Backward-compat: keep original stub function name for tests
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
