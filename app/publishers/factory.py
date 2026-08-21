from app.core.config import AppConfig
from app.publishers.base import Publisher
from app.publishers.instagram_publisher import InstagramPublisher
from app.publishers.telegram_publisher import TelegramPublisher
from app.publishers.tiktok_client import TokenStore
from app.publishers.tiktok_publisher import TikTokPublisher
from app.publishers.whatsapp_cloud_publisher import WhatsAppCloudPublisher
from app.publishers.whatsapp_publisher import WhatsAppPublisher


def build_publishers(
    config: AppConfig,
    tiktok_token_store: TokenStore | None = None,
) -> dict[str, Publisher]:
    publishers: dict[str, Publisher] = {
        "telegram": TelegramPublisher(config.bot_token, config.telegram_api),
    }
    if config.whatsapp_cloud is not None:
        publishers["whatsapp"] = WhatsAppCloudPublisher(
            access_token=config.whatsapp_cloud.access_token,
            phone_number_id=config.whatsapp_cloud.phone_number_id,
            api_base_url=config.whatsapp_cloud.api_base_url,
            api_version=config.whatsapp_cloud.api_version,
            request_timeout=config.whatsapp_cloud.request_timeout,
            media_max_bytes=config.whatsapp_cloud.media_max_bytes,
        )
    elif config.whatsapp is not None:
        publishers["whatsapp"] = WhatsAppPublisher(
            api_url=config.whatsapp.api_url,
            api_key=config.whatsapp.api_key,
            session_id=config.whatsapp.session_id,
            request_timeout=config.whatsapp.request_timeout,
            media_base_url=config.whatsapp.media_base_url,
            media_root=config.whatsapp.media_root,
            media_max_bytes=config.whatsapp.media_max_bytes,
        )
    if config.instagram is not None:
        publishers["instagram"] = InstagramPublisher(
            access_token=config.instagram.access_token,
            ig_user_id=config.instagram.ig_user_id,
            media_base_url=config.instagram.media_base_url,
            media_root=config.instagram.media_root,
            api_base_url=config.instagram.api_base_url,
            api_version=config.instagram.api_version,
            request_timeout=config.instagram.request_timeout,
            status_poll_interval=config.instagram.status_poll_interval,
            status_poll_attempts=config.instagram.status_poll_attempts,
        )
    if config.tiktok is not None:
        publishers["tiktok"] = TikTokPublisher(
            client_key=config.tiktok.client_key,
            client_secret=config.tiktok.client_secret,
            refresh_token=config.tiktok.refresh_token,
            media_base_url=config.tiktok.media_base_url,
            media_root=config.tiktok.media_root,
            api_base_url=config.tiktok.api_base_url,
            request_timeout=config.tiktok.request_timeout,
            privacy_level=config.tiktok.privacy_level,
            disable_comment=config.tiktok.disable_comment,
            disable_duet=config.tiktok.disable_duet,
            disable_stitch=config.tiktok.disable_stitch,
            auto_add_music=config.tiktok.auto_add_music,
            chunk_size=config.tiktok.chunk_size,
            status_poll_interval=config.tiktok.status_poll_interval,
            status_poll_attempts=config.tiktok.status_poll_attempts,
            token_store=tiktok_token_store,
        )
    return publishers
