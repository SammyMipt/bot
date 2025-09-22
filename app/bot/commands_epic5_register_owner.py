from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart

from app.core import callbacks, state_store
from app.core.auth import Identity, create_user, get_user_by_tg
from app.core.config import cfg
from app.db import repo_users

router = Router(name="epic5.register_owner")
OWNER_IDS: set[int] = set()
try:
    OWNER_IDS = {int(x) for x in cfg.telegram_owner_ids if x.isdigit()}
except Exception:
    OWNER_IDS = set()


def _op(op: str):
    def _f(cq: types.CallbackQuery) -> bool:
        try:
            op2, _ = callbacks.parse(cq.data)
            return op2 == op
        except Exception:
            return False

    return _f


def _eff_tg_id(raw_id: int) -> str:
    # AUTH_TG_OVERRIDE removed: always use the real Telegram id
    return str(raw_id)


def _start_kb(role: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🚀 Начать регистрацию",
                    callback_data=callbacks.build("ownreg", {}, role=role),
                )
            ]
        ]
    )


def _own_key(uid: int) -> str:
    return f"own:{uid}"


def _is_owner_message(m: types.Message) -> bool:
    try:
        return str(m.from_user.id) in cfg.telegram_owner_ids
    except Exception:
        return False


@router.message(CommandStart(), _is_owner_message)
async def owner_start(m: types.Message, actor: Identity):
    tg = _eff_tg_id(m.from_user.id)
    # Only react for predefined owner ids (guarded by filter)
    # Owner: always show entry to (re)start/continue owner setup
    if get_user_by_tg(tg):
        text = (
            "👋 Вы уже зарегистрированы как владелец.\n"
            "Можно продолжить настройку профиля владельца."
        )
    else:
        text = "👋 Добро пожаловать!\nВаш Telegram ID подтверждён как владелец курса."
    await m.answer(text, reply_markup=_start_kb(actor.role))


@router.message(Command("owner_start"))
async def owner_start_cmd(m: types.Message, actor: Identity):
    tg = _eff_tg_id(m.from_user.id)
    if get_user_by_tg(tg):
        await m.answer("Вы уже зарегистрированы как владелец.")
        return
    if tg not in cfg.telegram_owner_ids:
        await m.answer("⛔ Доступ запрещён")
        return
    await m.answer(
        "👋 Добро пожаловать!\nВаш Telegram ID подтверждён как владелец курса.",
        reply_markup=_start_kb(actor.role),
    )


@router.callback_query(_op("ownreg"))
async def owner_reg_start(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data)
    tg = _eff_tg_id(cq.from_user.id)
    if tg not in cfg.telegram_owner_ids:
        await cq.answer("⛔ Доступ запрещён", show_alert=True)
        return
    # Ensure owner user exists; if already exists, continue setup
    user = get_user_by_tg(tg)
    if not user:
        try:
            user = create_user(
                tg_id=tg, role="owner", name=cq.from_user.full_name or None
            )
        except Exception:
            user = None
    if not user:
        await cq.message.answer("⛔ Ошибка регистрации владельца")
        await cq.answer()
        return
    await cq.message.answer(
        "👨‍🏫 Хотите также работать как преподаватель?",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Да",
                        callback_data=callbacks.build("ownyes", {}, role=None),
                    ),
                    types.InlineKeyboardButton(
                        text="Нет",
                        callback_data=callbacks.build("ownno", {}, role=None),
                    ),
                ]
            ]
        ),
    )
    await cq.answer()


def _capacity_keyboard(page: int = 0, per_page: int = 10) -> types.InlineKeyboardMarkup:
    total = 50
    total_pages = (total + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    start = page * per_page + 1
    end = min(total, start + per_page - 1)
    rows: list[list[types.InlineKeyboardButton]] = []
    row: list[types.InlineKeyboardButton] = []
    for n in range(start, end + 1):
        row.append(
            types.InlineKeyboardButton(
                text=str(n),
                callback_data=callbacks.build("owncap", {"v": n}, role=None),
            )
        )
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="« Назад",
                callback_data=callbacks.build("ownpg", {"page": page - 1}, role=None),
            )
        )
    if page < total_pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                text="Вперёд »",
                callback_data=callbacks.build("ownpg", {"page": page + 1}, role=None),
            )
        )
    if nav:
        rows.append(nav)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(_op("ownyes"))
async def owner_yes_teacher(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data)
    await cq.message.answer(
        "Выберите вместимость (максимум студентов):",
        reply_markup=_capacity_keyboard(page=0),
    )
    await cq.answer()


@router.callback_query(_op("ownno"))
async def owner_no_teacher(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data)
    await cq.message.answer("Ок. Настройка преподавателя пропущена.")
    await cq.message.answer(
        "Как отобразить ваше имя?",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="✏️ Ввести ФИО",
                        callback_data=callbacks.build("ownnm", {}, role=None),
                    ),
                    types.InlineKeyboardButton(
                        text="✅ Оставить имя из Telegram",
                        callback_data=callbacks.build("owntg", {}, role=None),
                    ),
                ]
            ]
        ),
    )
    await cq.answer()


@router.callback_query(_op("ownpg"))
async def owner_capacity_page(cq: types.CallbackQuery, actor: Identity):
    _, payload = callbacks.extract(cq.data)
    page = int(payload.get("page", 0))
    try:
        await cq.message.edit_reply_markup(reply_markup=_capacity_keyboard(page=page))
    except Exception:
        await cq.message.answer(
            "Выберите вместимость (максимум студентов):",
            reply_markup=_capacity_keyboard(page=page),
        )
    await cq.answer()


@router.callback_query(_op("owncap"))
async def owner_capacity_pick(cq: types.CallbackQuery, actor: Identity):
    _, payload = callbacks.extract(cq.data)
    cap = int(payload.get("v", 0))
    tg = _eff_tg_id(cq.from_user.id)
    if cap < 1 or cap > 50:
        await cq.answer("Недопустимое значение", show_alert=True)
        return
    ok = repo_users.set_capacity_by_tg(tg, cap)
    if ok:
        await cq.message.answer(f"✅ Готово. Настроена вместимость: {cap}")
        await cq.message.answer(
            "Как отобразить ваше имя?",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="✏️ Ввести ФИО",
                            callback_data=callbacks.build("ownnm", {}, role=None),
                        ),
                        types.InlineKeyboardButton(
                            text="✅ Оставить имя из Telegram",
                            callback_data=callbacks.build("owntg", {}, role=None),
                        ),
                    ]
                ]
            ),
        )
    else:
        await cq.message.answer("⛔ Не удалось сохранить вместимость")
    await cq.answer()


@router.callback_query(_op("owntg"))
async def owner_name_use_tg(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data)
    tg = str(cq.from_user.id)
    name = cq.from_user.full_name or ""
    if not name.strip():
        await cq.message.answer(
            "Имя в Telegram пустое. Пожалуйста, введите ФИО вручную."
        )
    else:
        if repo_users.set_name_by_tg(tg, name):
            await cq.message.answer(f"✅ Имя установлено: {name}")
            await cq.message.answer("✅ Регистрация завершена")
        else:
            await cq.message.answer("⛔ Не удалось сохранить имя")
    await cq.answer()


@router.callback_query(_op("ownnm"))
async def owner_name_ask(cq: types.CallbackQuery, actor: Identity):
    callbacks.extract(cq.data)
    uid = cq.from_user.id
    state_store.put_at(_own_key(uid), "own", {"step": "name"}, ttl_sec=900)
    await cq.message.answer("✏️ Введите ваше ФИО (как показывать в системе):")
    await cq.answer()


def _awaits_owner_name(m: types.Message) -> bool:
    try:
        _, st = state_store.get(_own_key(m.from_user.id))
        return bool(st) and st.get("step") == "name"
    except Exception:
        return False


@router.message(F.text, _awaits_owner_name)
async def owner_name_set(m: types.Message, actor: Identity):
    # ignore commands
    if (m.text or "").strip().startswith("/"):
        return
    name = (m.text or "").strip()
    if not name:
        await m.answer(
            "Имя не может быть пустым. Попробуйте ещё раз или используйте имя из Telegram."
        )
        return
    tg = str(m.from_user.id)
    if repo_users.set_name_by_tg(tg, name):
        state_store.delete(_own_key(m.from_user.id))
        await m.answer(f"✅ Имя установлено: {name}")
        await m.answer("✅ Регистрация завершена")
    else:
        await m.answer("⛔ Не удалось сохранить имя")
