import asyncio
from pathlib import Path

from aiogram.client.telegram import SimpleFilesPathWrapper

from app.core.config import TelegramAPIConfig
from app.services.telegram_client import build_telegram_bot


def test_builds_local_bot_with_file_path_mapping() -> None:
    config = TelegramAPIConfig(
        base_url="http://telegram-bot-api:8081",
        local=True,
        server_files_path=Path("/server/files"),
        client_files_path=Path("/client/files"),
    )

    bot = build_telegram_bot("123456:TEST_TOKEN", config)

    try:
        assert bot.session.api.is_local
        assert bot.session.api.base == (
            "http://telegram-bot-api:8081/bot{token}/{method}"
        )
        wrapper = bot.session.api.wrap_local_file
        assert isinstance(wrapper, SimpleFilesPathWrapper)
        assert wrapper.to_local("/server/files/bot/video.mp4") == Path(
            "/client/files/bot/video.mp4"
        )
    finally:
        asyncio.run(bot.session.close())
