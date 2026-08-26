from app.core.config import AppConfig
from app.publishers.base import Publisher
from app.publishers.instagram_publisher import InstagramPublisher
from app.publishers.telegram_publisher import TelegramPublisher
from app.publishers.tiktok_publisher import TikTokPublisher
from app.publishers.whatsapp_publisher import WhatsAppPublisher


def build_publishers(config: AppConfig) -> dict[str, Publisher]:
    publishers: dict[str, Publisher] = {
        "telegram": TelegramPublisher(config.bot_token, config.telegram_api),
    }
    if config.whatsapp is not None:
        publishers["whatsapp"] = WhatsAppPublisher(
            api_token=config.whatsapp.api_token,
            api_url=config.whatsapp.api_url,
            request_timeout=config.whatsapp.request_timeout,
            media_max_bytes=config.whatsapp.media_max_bytes,
        )
    if config.instagram is not None:
        publishers["instagram"] = InstagramPublisher(
            api_key=config.instagram.api_key,
            account_id=config.instagram.account_id,
            api_base_url=config.instagram.api_base_url,
            request_timeout=config.instagram.request_timeout,
        )
    if config.tiktok is not None:
        publishers["tiktok"] = TikTokPublisher(
            api_key=config.tiktok.api_key,
            account_id=config.tiktok.account_id,
            api_base_url=config.tiktok.api_base_url,
            request_timeout=config.tiktok.request_timeout,
            privacy_level=config.tiktok.privacy_level,
        )
    return publishers
