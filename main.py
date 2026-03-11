import asyncio
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from config import load_config
from db.schema import init_db
from middlewares.auth import AuthMiddleware
from handlers import start, chat, pdf_upload, my_child, help, admin
from kb.chroma_client import init_chroma
from services.claude_client import init_claude
from services.scheduler import start_scheduler, stop_scheduler


async def main():
    config = load_config()

    logger.remove()
    logger.add(sys.stdout, level=config.log_level)
    logger.add("logs/parentbot.log", level=config.log_level, rotation="10 MB", retention="30 days")

    # Создать нужные директории
    os.makedirs(config.data_dir, exist_ok=True)
    os.makedirs(config.shared_kb_dir, exist_ok=True)
    os.makedirs(config.user_kb_dir, exist_ok=True)
    os.makedirs(config.chroma_dir, exist_ok=True)
    os.makedirs(os.path.dirname(config.db_path), exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    # Инициализировать БД
    await init_db(config.db_path, config.admin_telegram_id, config.whitelist_ids)

    # Инициализировать ChromaDB и Claude
    init_chroma(config.chroma_dir)
    init_claude(config.anthropic_api_key)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Middleware
    dp.message.middleware(AuthMiddleware(config))
    dp.callback_query.middleware(AuthMiddleware(config))

    # Роутеры (порядок важен: admin перед chat)
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(pdf_upload.router)
    dp.include_router(my_child.router)
    dp.include_router(chat.router)
    dp.include_router(help.router)

    # Планировщик — запускать через startup hook
    async def on_startup():
        start_scheduler(bot)

    async def on_shutdown():
        stop_scheduler()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("ParentBot запущен")
    await dp.start_polling(bot, config=config)


if __name__ == "__main__":
    asyncio.run(main())
