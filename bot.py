"""Точка входа: инициализация бота, БД, роутеров и polling."""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from database import close_db, init_db
from handlers import admin_handlers, engineer_handlers, user_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


async def main() -> None:
    if not settings.BOT_TOKEN or ":" not in settings.BOT_TOKEN:
        log.error("BOT_TOKEN не задан или некорректен. Проверьте .env")
        raise SystemExit(1)

    await init_db()
    log.info("БД готова: %s", settings.DB_PATH)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(admin_handlers.router, user_handlers.router, engineer_handlers.router)

    log.info("Бот запущен. Чат специалистов: %s", settings.GROUP_CHAT_ID)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
