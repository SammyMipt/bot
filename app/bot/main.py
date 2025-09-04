import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from app.bot.commands_epic4_owner import router as epic4_owner_router
from app.bot.commands_epic4_student import router as epic4_student_router
from app.bot.commands_epic4_teacher import router as epic4_teacher_router
from app.bot.commands_epic5_register import router as epic5_register_router
from app.bot.commands_epic5_register_owner import router as epic5_register_owner_router
from app.bot.demo_epic2 import router as demo_router
from app.bot.ui_owner_stub import router as ui_owner_stub_router
from app.core.cleanup import periodic_cleanup
from app.core.config import cfg
from app.core.logging import setup_logging

logging.getLogger(__name__).info("EPIC3 router included")


async def on_start(message: Message):
    # Deprecated: handled by epic5_register_router now
    await message.answer("/start работает через новый регистратор.")


async def main():
    setup_logging(logging.INFO)
    bot = Bot(cfg.telegram_token)
    dp = Dispatcher()

    # /start — handled in epic5_register_router
    # 🔽 EPIC-2: demo
    dp.include_router(demo_router)

    # 🔽 EPIC-3: auth middleware
    from app.bot.middleware.auth_mw import AuthMiddleware

    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

    # 🔽 EPIC-3: router with /whoami, /add_user
    from app.bot.commands_epic3 import router as epic3_router

    dp.include_router(epic3_router)
    # 🔽 OWNER UI: place before registration to avoid conflicts with broad text handlers
    dp.include_router(ui_owner_stub_router)
    # 🔽 EPIC-5: registration router
    dp.include_router(epic5_register_owner_router)
    dp.include_router(epic5_register_router)
    # 🔽 EPIC-4: order matters — owner/teacher first, then student
    dp.include_router(epic4_owner_router)
    dp.include_router(epic4_teacher_router)
    dp.include_router(epic4_student_router)

    asyncio.create_task(periodic_cleanup())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
