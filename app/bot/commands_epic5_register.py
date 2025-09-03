from __future__ import annotations

import logging
import re

from aiogram import F, Router, types
from aiogram.filters import CommandStart

from app.core import callbacks, state_store
from app.core.auth import Identity, get_user_by_tg
from app.core.config import cfg
from app.core.errors import StateNotFound
from app.db import repo_users
from app.db.conn import db

router = Router(name="epic5.register")
log = logging.getLogger(__name__)


def _op(op: str):
    def _f(cq: types.CallbackQuery) -> bool:
        try:
            op2, _ = callbacks.parse(cq.data)
            return op2 == op
        except Exception:
            return False

    return _f


def _uid(x: types.Message | types.CallbackQuery) -> int:
    return x.from_user.id


def _reg_key(uid: int) -> str:
    return f"reg:{uid}"


def _safe_get(key: str) -> dict | None:
    try:
        _, params = state_store.get(key)
        return params
    except StateNotFound:
        return None


def _start_keyboard(role: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🎓 Студент",
                    callback_data=callbacks.build("reg_s_role", {}, role=role),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="👨‍🏫 Преподаватель",
                    callback_data=callbacks.build("reg_t_role", {}, role=role),
                ),
            ],
        ]
    )


def _retry_cancel_kb(role: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🔄 Ввести заново",
                    callback_data=callbacks.build("reg_retry", {}, role=role),
                ),
                types.InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=callbacks.build("reg_back", {}, role=role),
                ),
            ]
        ]
    )


def _eff_tg_id(raw_id: int) -> str:
    return cfg.auth_tg_override or str(raw_id)


@router.message(CommandStart())
async def start(m: types.Message, actor: Identity):
    # If already registered (tg_id bound), greet and show role quick menu
    existing = get_user_by_tg(_eff_tg_id(m.from_user.id))
    if existing:
        role = existing.role
        await m.answer(
            f"Вы уже зарегистрированы как: {role}. Доступные команды зависят от роли."
        )
        return

    await m.answer(
        "👋 Добро пожаловать в курс физики для будущих ML‑специалистов!\n"
        "Этот бот поможет вам с учёбой: вы сможете получать материалы, сдавать работы и записываться на сдачи.\n"
        "К сожалению, мы пока не знакомы.\n"
        "Выберите роль для регистрации:",
        reply_markup=_start_keyboard(actor.role),
    )


@router.callback_query(_op("reg_menu"))
async def reg_menu(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data, expected_role=actor.role)
    uid = _uid(cq)
    try:
        state_store.delete(_reg_key(uid))
    except Exception:
        pass
    await cq.message.answer("Ок. Открываю главное меню.")
    await cq.answer()


@router.callback_query(_op("reg_t_role"))
async def reg_teacher(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data, expected_role=actor.role)
    if get_user_by_tg(_eff_tg_id(cq.from_user.id)):
        await cq.answer("Уже зарегистрированы", show_alert=True)
        return
    uid = _uid(cq)
    state_store.put_at(
        _reg_key(uid),
        "reg",
        {"role": "t", "step": "code", "attempts": 0},
        ttl_sec=900,
    )
    await cq.message.answer(
        "Введите секретный код курса:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=callbacks.build("reg_back", {}, role=actor.role),
                    )
                ]
            ]
        ),
    )
    await cq.answer()


@router.callback_query(_op("reg_s_role"))
async def reg_student(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data, expected_role=actor.role)
    if get_user_by_tg(_eff_tg_id(cq.from_user.id)):
        await cq.answer("Уже зарегистрированы", show_alert=True)
        return
    uid = _uid(cq)
    state_store.put_at(
        _reg_key(uid),
        "reg",
        {"role": "s", "step": "email", "attempts": 0},
        ttl_sec=900,
    )
    await cq.message.answer(
        "✉️ Введите ваш e‑mail как в LMS (Moodle):",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=callbacks.build("reg_back", {}, role=actor.role),
                    )
                ]
            ]
        ),
    )
    await cq.answer()


def _has_mode(uid: int, mode: str) -> bool:
    st = _safe_get(_reg_key(uid))
    return bool(st and st.get("mode") == mode)


@router.message(F.text)
async def reg_input_text(m: types.Message, actor: Identity):
    if get_user_by_tg(_eff_tg_id(m.from_user.id)):
        return  # already registered; ignore
    uid = _uid(m)
    st = _safe_get(_reg_key(uid)) or {}
    role = st.get("role")
    step = st.get("step")
    attempts = int(st.get("attempts") or 0)

    if role == "t" and step == "code":
        env_code = (cfg.course_secret or "").strip()
        secret = (m.text or "").strip()
        if not env_code or secret != env_code:
            attempts += 1
            state_store.put_at(
                _reg_key(uid),
                "reg",
                {"role": "t", "step": "code", "attempts": attempts},
                ttl_sec=900,
            )
            log.warning("[reg] teacher code invalid (attempt %s)", attempts)
            if attempts >= 3:
                await m.answer(
                    "⛔ Неверный код. Попробуйте начать заново.",
                    reply_markup=_start_keyboard(actor.role),
                )
            else:
                await m.answer(
                    "⛔ Неверный код",
                    reply_markup=_retry_cancel_kb(actor.role),
                )
            return
        # code OK → list free teachers
        candidates = repo_users.find_all_teachers_for_bind()
        if not candidates:
            log.info("[reg] teacher no candidates after valid code")
            state_store.delete(_reg_key(uid))
            await m.answer(
                "Не найден свободный профиль преподавателя. Вы в списке непривязанных. Свяжитесь с владельцем."
            )
            return
        # always show list — paginate
        ids = [c["id"] for c in candidates]
        state_store.put_at(
            _reg_key(uid),
            "reg",
            {"role": "t", "step": "list", "page": 0, "ids": ids},
            ttl_sec=900,
        )
        await _send_candidates_list(m, role="t", page=0, ids=ids)
        return

    if role == "s" and step == "email":
        email = (m.text or "").strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""):
            attempts += 1
            state_store.put_at(
                _reg_key(uid),
                "reg",
                {"role": "s", "step": "email", "attempts": attempts},
                ttl_sec=900,
            )
            msg = "Некорректный e-mail."
            if attempts >= 3:
                await m.answer(
                    msg + " Попробуйте начать заново.",
                    reply_markup=_start_keyboard(actor.role),
                )
            else:
                await m.answer(msg, reply_markup=_retry_cancel_kb(actor.role))
            return
        candidates = repo_users.find_students_by_email(email.lower())
        if not candidates:
            log.info("[reg] student not found by email=%s", email)
            # keep state but allow retry/back; if email exists but bound, show dedicated message
            if repo_users.is_student_email_bound(email.lower()):
                await m.answer(
                    "⛔ Ваш e‑mail уже привязан к другому Telegram аккаунту. Обратитесь к владельцу",
                    reply_markup=_retry_cancel_kb(actor.role),
                )
            else:
                await m.answer(
                    "⛔ Запись не найдена. Проверьте e‑mail или обратитесь к владельцу",
                    reply_markup=_retry_cancel_kb(actor.role),
                )
            return
        if len(candidates) == 1:
            cand = candidates[0]
            state_store.put_at(
                _reg_key(uid),
                "reg",
                {"role": "s", "step": "confirm", "user_id": cand["id"]},
                ttl_sec=900,
            )
            name = cand.get("name") or "Без имени"
            group = cand.get("group_name") or "—"
            email = cand.get("email") or "—"
            await m.answer(
                "Найден профиль студента:\n"
                f"👤 {name}\n"
                f"🎓 Группа: {group}\n"
                f"✉️ E‑mail: {email}\n\n"
                "Подтвердить привязку?",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text="✅ Подтвердить",
                                callback_data=callbacks.build(
                                    "reg_confirm_yes", {}, role=actor.role
                                ),
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="⬅️ Назад",
                                callback_data=callbacks.build(
                                    "reg_back", {}, role=actor.role
                                ),
                            )
                        ],
                    ]
                ),
            )
            return
        ids = [c["id"] for c in candidates]
        state_store.put_at(
            _reg_key(uid),
            "reg",
            {"role": "s", "step": "list", "page": 0, "ids": ids},
            ttl_sec=900,
        )
        await _send_candidates_list(m, role="s", page=0, ids=ids)
        return
    # no active registration state — ignore


@router.callback_query(_op("reg_retry"))
async def reg_retry(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data, expected_role=actor.role)
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    role = st.get("role")
    step = st.get("step")
    if role == "t" and step in ("code", "list", "confirm"):
        state_store.put_at(
            _reg_key(uid),
            "reg",
            {"role": "t", "step": "code", "attempts": 0},
            ttl_sec=900,
        )
        await cq.message.answer("Введите секретный код курса:")
    elif role == "s" and step in ("email", "list", "confirm"):
        state_store.put_at(
            _reg_key(uid),
            "reg",
            {"role": "s", "step": "email", "attempts": 0},
            ttl_sec=900,
        )
        await cq.message.answer("✉️ Введите ваш e‑mail как в LMS (Moodle):")
    else:
        await cq.message.answer("Начните заново: /start")
    await cq.answer()


@router.callback_query(_op("reg_back"))
async def reg_back(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data, expected_role=actor.role)
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    role = st.get("role")
    if role:
        # go back to role selection
        state_store.put_at(_reg_key(uid), "reg", {"step": "choose"}, ttl_sec=900)
    await cq.message.answer(
        "👋 Добро пожаловать в курс физики для будущих ML‑специалистов!\n"
        "Этот бот поможет вам с учёбой: вы сможете получать материалы, сдавать работы и записываться на сдачи.\n"
        "К сожалению, мы пока не знакомы.\n"
        "Выберите роль для регистрации:",
        reply_markup=_start_keyboard(actor.role),
    )
    await cq.answer()


def _load_labels(ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    with db() as conn:
        rows = conn.execute(
            f"SELECT id, name, group_name, role FROM users WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
    labels: dict[str, str] = {}
    for r in rows:
        uid, name, group_name, role = r[0], r[1], r[2], r[3]
        base = name or f"ID {uid}"
        if role == "student" and group_name:
            base = f"{base} · {group_name}"
        labels[uid] = base
    return labels


def _list_keyboard(
    role: str, page: int, total_pages: int, ids: list[str]
) -> types.InlineKeyboardMarkup:
    per_page = 10
    start = page * per_page
    chunk = ids[start : start + per_page]
    labels = _load_labels(chunk)
    rows: list[list[types.InlineKeyboardButton]] = []
    for uid in chunk:
        txt = labels.get(uid, f"ID {uid}")
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=txt,
                    callback_data=callbacks.build("reg_pick", {"uid": uid}),
                )
            ]
        )
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=callbacks.build("reg_page", {"page": page - 1}),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=callbacks.build("reg_page", {"page": page + 1}),
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=callbacks.build("reg_back", {}),
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_candidates_list(m: types.Message, role: str, page: int, ids: list[str]):
    per_page = 10
    total_pages = max(1, (len(ids) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    header = (
        "Нашлось несколько записей. Выберите вашу:"
        if role == "s"
        else "Выберите профиль преподавателя:"
    )
    await m.answer(header, reply_markup=_list_keyboard(role, page, total_pages, ids))


@router.callback_query(_op("reg_page"))
async def reg_page(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    page = int(payload.get("page", 0))
    ids = st.get("ids") or []
    role = st.get("role") or "s"
    if not ids:
        await cq.answer()
        return
    state_store.put_at(
        _reg_key(uid),
        "reg",
        {"role": role, "step": "list", "page": page, "ids": ids},
        ttl_sec=900,
    )
    try:
        await cq.message.edit_reply_markup(
            reply_markup=_list_keyboard(role, page, max(1, (len(ids) + 9) // 10), ids)
        )
    except Exception:
        await _send_candidates_list(cq.message, role=role, page=page, ids=ids)
    await cq.answer()


@router.callback_query(_op("reg_pick"))
async def reg_pick(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    role = st.get("role") or "s"
    _, payload = callbacks.extract(cq.data, expected_role=actor.role)
    user_id = payload.get("uid")
    # Teacher flow: prevent picking already bound teacher
    if role == "t" and user_id and repo_users.is_user_bound(user_id):
        await cq.message.answer(
            "⛔ Этот преподаватель уже зарегистрирован. Обратитесь к владельцу"
        )
        await cq.answer()
        return
    state_store.put_at(
        _reg_key(uid),
        "reg",
        {"role": role, "step": "confirm", "user_id": user_id},
        ttl_sec=900,
    )
    if role == "t":
        info = repo_users.get_user_brief(user_id) if user_id else None
        name = (info or {}).get("name") or "Без имени"
        header = f"Вы уверены, что хотите зарегистрироваться как {name}?"
    else:
        header = "Профиль выбран. Подтвердить привязку?"
    await cq.message.answer(
        header,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=callbacks.build(
                            "reg_confirm_yes", {}, role=actor.role
                        ),
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=callbacks.build("reg_back", {}, role=actor.role),
                    )
                ],
            ]
        ),
    )
    await cq.answer()


@router.callback_query(_op("reg_confirm_yes"))
async def reg_confirm(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data, expected_role=actor.role)
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    user_id = st.get("user_id")
    if not user_id:
        await cq.answer("Нет выбранного профиля", show_alert=True)
        return
    eff = _eff_tg_id(uid)
    if repo_users.is_tg_bound(eff):
        state_store.delete(_reg_key(uid))
        await cq.message.answer("Этот аккаунт уже привязан. Открываю главное меню.")
        await cq.answer()
        return
    ok = repo_users.bind_tg(user_id, eff)
    if not ok:
        await cq.message.answer(
            "E_ALREADY_BOUND — Запись уже привязана. Попробуйте начать заново."
        )
        await cq.answer()
        return
    log.info("[reg] bound tg=%s to user_id=%s", eff, user_id)
    state_store.delete(_reg_key(uid))
    await cq.message.answer("✅ Регистрация завершена")
    await cq.answer()
