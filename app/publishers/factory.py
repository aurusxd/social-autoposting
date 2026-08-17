from app.core.config import AppConfig
from app.publishers.base import Publisher
from app.publishers.telegram_publisher import TelegramPublisher


def build_publishers(config: AppConfig) -> dict[str, Publisher]:
    return {
        "telegram": TelegramPublisher(config.bot_token),
    }
