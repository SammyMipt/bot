import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.core.config import cfg
from app.core.logging import setup_logging


async def on_start(message: Message):
    await message.answer("Привет! Это скелет бота. /start")


async def main():
    setup_logging(logging.INFO)
    bot = Bot(cfg.telegram_token)
    dp = Dispatcher()
    dp.message.register(on_start, CommandStart())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
