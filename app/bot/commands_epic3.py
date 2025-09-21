import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.core import auth
from app.core.roles import OWNER
from app.db.conn import db

logger = logging.getLogger(__name__)

router = Router()


def _fmt_profile_card(
    role: str,
    name: str | None,
    email: str | None,
    group_name: str | None,
    capacity: int | None,
    tg_bound: bool,
    active: bool,
) -> tuple[str, str]:
    role_emoji = (
        "👑"
        if role == "owner"
        else ("👨‍🏫" if role == "teacher" else ("🎓" if role == "student" else "👤"))
    )
    status_emoji = "🟢" if active else "⚪️"
    tg_emoji = "🟢" if tg_bound else "⚪️"
    nm = name or "(без имени)"
    lines: list[str] = [
        f"<b>{nm}</b>",
        f"<b>Роль:</b> {role_emoji} {role}",
        f"<b>Email:</b> {email or '—'}",
        f"<b>Статус:</b> {status_emoji} {'активен' if active else 'неактивен'}",
    ]
    if role == "student":
        lines.append(f"<b>Группа:</b> {group_name or '—'}")
    if role == "teacher":
        lines.append(
            f"<b>Максимум студентов:</b> {capacity if capacity is not None else '—'}"
        )
    lines.append(f"<b>TG:</b> {tg_emoji} {'привязан' if tg_bound else 'не привязан'}")
    return ("\n".join(lines), "HTML")


@router.message(Command("whoami"))
async def whoami(msg: Message, actor: auth.Identity):
    logger.info(f"/whoami called by tg_id={msg.from_user.id}")
    with db() as conn:
        row = conn.execute(
            (
                "SELECT role, name, email, group_name, capacity, tg_id, is_active "
                "FROM users WHERE tg_id = ? LIMIT 1"
            ),
            (actor.tg_id,),
        ).fetchone()
    if not row:
        text = (
            f"<b>{actor.name or '(без имени)'}</b>\n"
            f"<b>Роль:</b> {actor.role}\n"
            f"<b>TG:</b> не привязан"
        )
        return await msg.answer(text, parse_mode="HTML")
    role, name, email, group_name, capacity, tg_id, is_active = (
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
        row[5],
        row[6],
    )
    text, mode = _fmt_profile_card(
        role=role or "",
        name=name,
        email=email,
        group_name=group_name,
        capacity=capacity,
        tg_bound=bool(tg_id),
        active=bool(int(is_active or 0) == 1),
    )
    await msg.answer(text, parse_mode=mode)


@router.message(Command("add_user"))
async def add_user(msg: Message, actor: auth.Identity):
    logger.info(f"/add_user called by tg_id={msg.from_user.id}, text={msg.text}")
    if actor.role != OWNER:
        logger.warning("forbidden: not an owner")
        return await msg.answer("forbidden: owner only")
    parts = msg.text.split(maxsplit=3)
    if len(parts) < 4:
        return await msg.answer("usage: /add_user <role> <tg_id> <name>")
    role, tg_id, name = parts[1], parts[2], parts[3]
    try:
        user = auth.create_user(tg_id, role, name=name)
    except AssertionError:
        logger.error(f"invalid role requested: {role}")
        return await msg.answer("invalid role (owner/teacher/student)")
    logger.info(f"user created: {user}")
    await msg.answer(
        f"created: id={user.id} role={user.role} tg={user.tg_id} name={user.name}"
    )
