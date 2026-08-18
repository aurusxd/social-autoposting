import asyncio

from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand
from loguru import logger

from app.bot.handlers import router
from app.bot.middleware import OwnerOnlyMiddleware
from app.core.config import load_config
from app.services.telegram_client import build_telegram_bot


async def run() -> None:
    config = load_config()
    bot = build_telegram_bot(
        config.bot_token,
        config.telegram_api,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(
        storage=MemoryStorage(),
        events_isolation=SimpleEventIsolation(),
    )
    owner_only = OwnerOnlyMiddleware(config.owner_id)
    dispatcher.message.outer_middleware(owner_only)
    dispatcher.callback_query.outer_middleware(owner_only)
    dispatcher.include_router(router)
    await bot.set_my_commands(
        [
            BotCommand(command="new", description="Создать новый пост"),
            BotCommand(command="cancel", description="Удалить текущий черновик"),
        ]
    )
    logger.info("Starting Telegram UI with {} publication targets", len(config.targets))
    await dispatcher.start_polling(bot, app_config=config)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
