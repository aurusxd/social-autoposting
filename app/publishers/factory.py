from app.core.config import AppConfig
from app.publishers.base import Publisher
from app.publishers.instagram_publisher import InstagramPublisher
from app.publishers.telegram_publisher import TelegramPublisher


def build_publishers(config: AppConfig) -> dict[str, Publisher]:
    publishers: dict[str, Publisher] = {
        "telegram": TelegramPublisher(config.bot_token),
    }
    if config.instagram is not None:
        publishers["instagram"] = InstagramPublisher(
            username=config.instagram.username,
            password=config.instagram.password,
            totp_secret=config.instagram.totp_secret,
            session_path=config.instagram.session_path,
            proxy=config.instagram.proxy,
            request_timeout=config.instagram.request_timeout,
        )
    return publishers
