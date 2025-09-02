from __future__ import annotations

import logging
import re

from aiogram import F, Router, types
from aiogram.filters import CommandStart

from app.core import state_store
from app.core.auth import Identity, get_user_by_tg
from app.core.config import cfg
from app.core.errors import StateNotFound
from app.db import repo_users
from app.db.conn import db

router = Router(name="epic5.register")
log = logging.getLogger(__name__)


def _uid(x: types.Message | types.CallbackQuery) -> int:
    return x.from_user.id


def _reg_key(uid: int) -> str:
    return f"reg:{uid}"


def _safe_get(key: str) -> dict | None:
    try:
        return state_store.get(key)
    except StateNotFound:
        return None


def _start_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="👨‍🏫 Преподаватель", callback_data="reg:t:role"
                ),
                types.InlineKeyboardButton(
                    text="🎓 Студент", callback_data="reg:s:role"
                ),
            ]
        ]
    )


def _retry_cancel_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="Повторить", callback_data="reg:retry"),
                types.InlineKeyboardButton(text="⬅️ Назад", callback_data="reg:back"),
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
        "Добро пожаловать! Кем вы являетесь?",
        reply_markup=_start_keyboard(),
    )


@router.callback_query(F.data == "reg:menu")
async def reg_menu(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    try:
        state_store.delete(_reg_key(uid))
    except Exception:
        pass
    await cq.message.answer("Ок. Открываю главное меню.")
    await cq.answer()


@router.callback_query(F.data == "reg:t:role")
async def reg_teacher(cq: types.CallbackQuery, actor: Identity):
    if get_user_by_tg(_eff_tg_id(cq.from_user.id)):
        await cq.answer("Уже зарегистрированы", show_alert=True)
        return
    uid = _uid(cq)
    state_store.put_at(
        _reg_key(uid), {"role": "t", "step": "code", "attempts": 0}, ttl_sec=900
    )
    await cq.message.answer(
        "Введите секретный код курса:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="reg:back")]
            ]
        ),
    )
    await cq.answer()


@router.callback_query(F.data == "reg:s:role")
async def reg_student(cq: types.CallbackQuery, actor: Identity):
    if get_user_by_tg(_eff_tg_id(cq.from_user.id)):
        await cq.answer("Уже зарегистрированы", show_alert=True)
        return
    uid = _uid(cq)
    state_store.put_at(
        _reg_key(uid), {"role": "s", "step": "email", "attempts": 0}, ttl_sec=900
    )
    await cq.message.answer(
        "Введите e-mail, указанный при регистрации у владельца (3 попытки):",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="reg:back")]
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
                {"role": "t", "step": "code", "attempts": attempts},
                ttl_sec=900,
            )
            log.warning("[reg] teacher code invalid (attempt %s)", attempts)
            if attempts >= 3:
                await m.answer(
                    "E_SECRET_CODE_INVALID — Неверный код. Попробуйте начать заново.",
                    reply_markup=_start_keyboard(),
                )
            else:
                await m.answer(
                    "E_SECRET_CODE_INVALID — Неверный код.",
                    reply_markup=_retry_cancel_kb(),
                )
            return
        # code OK → list free teachers
        candidates = repo_users.find_free_teachers_for_bind()
        if not candidates:
            log.info("[reg] teacher no candidates after valid code")
            state_store.delete(_reg_key(uid))
            await m.answer(
                "Не найден свободный профиль преподавателя. Вы в списке непривязанных. Свяжитесь с владельцем."
            )
            return
        if len(candidates) == 1:
            cand = candidates[0]
            state_store.put_at(
                _reg_key(uid),
                {"role": "t", "step": "confirm", "user_id": cand["id"]},
                ttl_sec=900,
            )
            await m.answer(
                f"Профиль найден: {cand.get('name') or 'Без имени'}\nПодтвердить привязку?",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text="Подтвердить", callback_data="reg:confirm:yes"
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="⬅️ Назад", callback_data="reg:back"
                            )
                        ],
                    ]
                ),
            )
            return
        # multiple candidates — paginate
        ids = [c["id"] for c in candidates]
        state_store.put_at(
            _reg_key(uid),
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
                {"role": "s", "step": "email", "attempts": attempts},
                ttl_sec=900,
            )
            msg = "Некорректный e-mail."
            if attempts >= 3:
                await m.answer(
                    msg + " Попробуйте начать заново.", reply_markup=_start_keyboard()
                )
            else:
                await m.answer(msg, reply_markup=_retry_cancel_kb())
            return
        candidates = repo_users.find_students_by_email(email.lower())
        if not candidates:
            log.info("[reg] student not found by email=%s", email)
            # keep state but allow retry/back
            await m.answer(
                "E_STUDENT_NOT_FOUND — Запись не найдена. Проверьте e-mail или свяжитесь с владельцем.",
                reply_markup=_retry_cancel_kb(),
            )
            return
        if len(candidates) == 1:
            cand = candidates[0]
            state_store.put_at(
                _reg_key(uid),
                {"role": "s", "step": "confirm", "user_id": cand["id"]},
                ttl_sec=900,
            )
            desc = f"{cand.get('name') or 'Без имени'}" + (
                f", группа {cand.get('group_name')}" if cand.get("group_name") else ""
            )
            await m.answer(
                f"Профиль найден: {desc}\nПодтвердить привязку?",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text="Подтвердить", callback_data="reg:confirm:yes"
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="⬅️ Назад", callback_data="reg:back"
                            )
                        ],
                    ]
                ),
            )
            return
        ids = [c["id"] for c in candidates]
        state_store.put_at(
            _reg_key(uid),
            {"role": "s", "step": "list", "page": 0, "ids": ids},
            ttl_sec=900,
        )
        await _send_candidates_list(m, role="s", page=0, ids=ids)
        return
    # no active registration state — ignore


@router.callback_query(F.data == "reg:retry")
async def reg_retry(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    role = st.get("role")
    step = st.get("step")
    if role == "t" and step in ("code", "list", "confirm"):
        state_store.put_at(
            _reg_key(uid), {"role": "t", "step": "code", "attempts": 0}, ttl_sec=900
        )
        await cq.message.answer("Введите секретный код курса:")
    elif role == "s" and step in ("email", "list", "confirm"):
        state_store.put_at(
            _reg_key(uid), {"role": "s", "step": "email", "attempts": 0}, ttl_sec=900
        )
        await cq.message.answer("Введите e-mail, указанный в LMS:")
    else:
        await cq.message.answer("Начните заново: /start")
    await cq.answer()


@router.callback_query(F.data == "reg:back")
async def reg_back(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    role = st.get("role")
    if role:
        # go back to role selection
        state_store.put_at(_reg_key(uid), {"step": "choose"}, ttl_sec=900)
    await cq.message.answer("Кем вы являетесь?", reply_markup=_start_keyboard())
    await cq.answer()


def _load_labels(ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    with db() as conn:
        rows = conn.execute(
            f"SELECT id, name, group_name, role FROM users WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
    labels: dict[int, str] = {}
    for r in rows:
        uid, name, group_name, role = int(r[0]), r[1], r[2], r[3]
        base = name or f"ID {uid}"
        if role == "student" and group_name:
            base = f"{base} · {group_name}"
        labels[uid] = base
    return labels


def _list_keyboard(
    role: str, page: int, total_pages: int, ids: list[int]
) -> types.InlineKeyboardMarkup:
    per_page = 10
    start = page * per_page
    chunk = ids[start : start + per_page]
    labels = _load_labels(chunk)
    rows: list[list[types.InlineKeyboardButton]] = []
    for uid in chunk:
        txt = labels.get(uid, f"ID {uid}")
        rows.append(
            [types.InlineKeyboardButton(text=txt, callback_data=f"reg:pick:{uid}")]
        )
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад", callback_data=f"reg:page:{page - 1}"
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »", callback_data=f"reg:page:{page + 1}"
            )
        )
    if nav:
        rows.append(nav)
    rows.append([types.InlineKeyboardButton(text="⬅️ Назад", callback_data="reg:back")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_candidates_list(m: types.Message, role: str, page: int, ids: list[int]):
    per_page = 10
    total_pages = max(1, (len(ids) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    header = (
        "Нашлось несколько записей. Выберите вашу:"
        if role == "s"
        else "Выберите профиль преподавателя:"
    )
    await m.answer(header, reply_markup=_list_keyboard(role, page, total_pages, ids))


@router.callback_query(F.data.regexp(r"^reg:page:(\d+)$"))
async def reg_page(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    page = int(cq.data.split(":")[2])
    ids = st.get("ids") or []
    role = st.get("role") or "s"
    if not ids:
        await cq.answer()
        return
    state_store.put_at(
        _reg_key(uid),
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


@router.callback_query(F.data.regexp(r"^reg:pick:(\d+)$"))
async def reg_pick(cq: types.CallbackQuery, actor: Identity):
    uid = _uid(cq)
    st = _safe_get(_reg_key(uid)) or {}
    role = st.get("role") or "s"
    user_id = int(cq.data.split(":")[2])
    state_store.put_at(
        _reg_key(uid),
        {"role": role, "step": "confirm", "user_id": user_id},
        ttl_sec=900,
    )
    await cq.message.answer(
        "Профиль выбран. Подтвердить привязку?",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Подтвердить", callback_data="reg:confirm:yes"
                    )
                ],
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="reg:back")],
            ]
        ),
    )
    await cq.answer()


@router.callback_query(F.data == "reg:confirm:yes")
async def reg_confirm(cq: types.CallbackQuery, actor: Identity):
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
    ok = repo_users.bind_tg(int(user_id), eff)
    if not ok:
        await cq.message.answer(
            "E_ALREADY_BOUND — Запись уже привязана. Попробуйте начать заново."
        )
        await cq.answer()
        return
    log.info("[reg] bound tg=%s to user_id=%s", eff, user_id)
    state_store.delete(_reg_key(uid))
    await cq.message.answer("Готово! Вы привязаны к профилю. Открываю главное меню.")
    await cq.answer()
