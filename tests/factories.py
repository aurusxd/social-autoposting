from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import (
    AppConfig,
    InstagramConfig,
    PublishTarget,
    TelegramAPIConfig,
    TikTokConfig,
    WebConfig,
    WhatsAppConfig,
)

SECRET_KEY = "test-secret-key-that-is-long-enough-for-config"
PASSWORD = "correct horse battery staple"


def web_config(**overrides: Any) -> WebConfig:
    values: dict[str, Any] = {
        "username": "admin",
        "password": PASSWORD,
        "password_hash": "",
        "secret_key": SECRET_KEY,
        "session_max_age": 3600,
        "max_upload_bytes": 8 * 1024**2,
        "secure_cookies": False,
    }
    values.update(overrides)
    return WebConfig(**values)


def telegram_api_config(**overrides: Any) -> TelegramAPIConfig:
    values: dict[str, Any] = {
        "base_url": "https://api.telegram.org",
        "local": False,
        "server_files_path": Path("/var/lib/telegram-bot-api"),
        "client_files_path": Path("/var/lib/telegram-bot-api"),
    }
    values.update(overrides)
    return TelegramAPIConfig(**values)


def app_config(
    *,
    targets: tuple[PublishTarget, ...] = (),
    whatsapp: WhatsAppConfig | None = None,
    instagram: InstagramConfig | None = None,
    tiktok: TikTokConfig | None = None,
    web: WebConfig | None = None,
    telegram_api: TelegramAPIConfig | None = None,
    bot_token: str = "telegram-token",
) -> AppConfig:
    return AppConfig(
        bot_token=bot_token,
        web=web or web_config(),
        telegram_api=telegram_api or telegram_api_config(),
        targets=targets,
        whatsapp=whatsapp,
        instagram=instagram,
        tiktok=tiktok,
    )
