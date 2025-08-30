from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.core import callbacks

router = Router()


@router.message(Command("demo"))
async def demo_help(msg: types.Message):
    data = {"hello": "world", "by": "epic2"}
    cb = callbacks.build(op="DEMO", value=data, role=None, ttl_sec=60)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Жми меня (EPIC-2)", callback_data=cb)]
        ]
    )
    await msg.answer("Демо EPIC-2: кнопка хранит payload на 60с", reply_markup=kb)


@router.callback_query(F.data.startswith("DEMO:"))
async def on_demo(cbq: CallbackQuery):
    op, payload = callbacks.extract(cbq.data, expected_role=None)
    await cbq.message.edit_text(f"Callback {op} ок, payload: {payload}")
    await cbq.answer("OK")
