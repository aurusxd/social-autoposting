from __future__ import annotations

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import SimpleFilesPathWrapper, TelegramAPIServer

from app.core.config import TelegramAPIConfig


def build_telegram_bot(
    token: str,
    api_config: TelegramAPIConfig,
    default: DefaultBotProperties | None = None,
) -> Bot:
    options: dict[str, object] = {"is_local": api_config.local}
    if api_config.local:
        options["wrap_local_file"] = SimpleFilesPathWrapper(
            server_path=api_config.server_files_path,
            local_path=api_config.client_files_path,
        )
    api = TelegramAPIServer.from_base(api_config.base_url, **options)
    session = AiohttpSession(api=api)
    return Bot(token=token, session=session, default=default)
