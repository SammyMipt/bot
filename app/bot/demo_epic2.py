from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.core import callbacks
from app.core.auth import Identity

router = Router()


@router.message(Command("demo"))
async def demo_help(msg: types.Message, actor: Identity):
    data = {"hello": "world", "by": "epic2"}
    cb = callbacks.build(op="DEMO", params=data, role=actor.role, ttl_sec=60)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Жми меня (EPIC-2)", callback_data=cb)]
        ]
    )
    await msg.answer("Демо EPIC-2: кнопка хранит payload на 60с", reply_markup=kb)


@router.callback_query(F.data.startswith("DEMO:"))
async def on_demo(cbq: CallbackQuery, actor: Identity):
    action, payload = callbacks.extract(cbq.data, expected_role=actor.role)
    await cbq.message.edit_text(f"Callback {action} ок, payload: {payload}")
    await cbq.answer("OK")
