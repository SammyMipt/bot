import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.core import auth
from app.core.roles import OWNER

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("whoami"))
async def whoami(msg: Message, actor: auth.Identity):
    logger.info(f"/whoami called by tg_id={msg.from_user.id}")
    await msg.answer(
        f"you are id={actor.id} role={actor.role} tg={actor.tg_id} name={actor.name}"
    )


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
