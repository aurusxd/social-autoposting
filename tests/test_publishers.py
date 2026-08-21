import asyncio
from pathlib import Path

from app.core.config import (
    AppConfig,
    InstagramConfig,
    TelegramAPIConfig,
    TikTokConfig,
    WhatsAppCloudConfig,
    WhatsAppConfig,
)
from app.publishers import Post, PublishResult, PublishTarget
from app.publishers.factory import build_publishers
from app.publishers.fakes import FakePublisher
from app.publishers.instagram_publisher import InstagramPublisher
from app.publishers.tiktok_publisher import TikTokPublisher
from app.publishers.whatsapp_cloud_publisher import WhatsAppCloudPublisher
from app.publishers.whatsapp_publisher import WhatsAppPublisher


def _telegram_api() -> TelegramAPIConfig:
    return TelegramAPIConfig(
        base_url="https://api.telegram.org",
        local=False,
        server_files_path=Path("/var/lib/telegram-bot-api"),
        client_files_path=Path("/var/lib/telegram-bot-api"),
    )


def test_fake_publisher_records_calls_and_result() -> None:
    expected = PublishResult(success=False, retryable=True, error="timeout")
    publisher = FakePublisher(platform="telegram", result=expected)
    post = Post(id=1, caption="Тест")
    target = PublishTarget(key="-1001", kind="channel", name="Основной")

    result = asyncio.run(publisher.publish(post, target))

    assert result == expected
    assert publisher.calls == [(post, target)]


def test_publisher_factory_registers_enabled_instagram() -> None:
    config = AppConfig(
        bot_token="telegram-token",
        owner_id=123,
        telegram_api=_telegram_api(),
        targets=(),
        whatsapp_engine="openwa",
        whatsapp=None,
        whatsapp_cloud=None,
        instagram=InstagramConfig(
            access_token="graph-token",
            ig_user_id="instagram-account",
            api_base_url="https://graph.example",
            api_version="v25.0",
            request_timeout=90,
            media_base_url="https://media.example",
            media_root=Path("media"),
            status_poll_interval=1,
            status_poll_attempts=3,
        ),
        tiktok=None,
    )

    publishers = build_publishers(config)

    publisher = publishers["instagram"]
    assert isinstance(publisher, InstagramPublisher)
    assert publisher.ig_user_id == "instagram-account"
    assert publisher.account_url == "https://graph.example/v25.0/instagram-account"


def test_publisher_factory_registers_enabled_tiktok() -> None:
    config = AppConfig(
        bot_token="telegram-token",
        owner_id=123,
        telegram_api=_telegram_api(),
        targets=(),
        whatsapp_engine="openwa",
        whatsapp=None,
        whatsapp_cloud=None,
        instagram=None,
        tiktok=TikTokConfig(
            client_key="client-key",
            client_secret="client-secret",
            refresh_token="refresh-token",
            api_base_url="https://open.tiktokapis.example",
            request_timeout=90,
            privacy_level="SELF_ONLY",
            media_base_url="https://media.example",
            media_root=Path("media"),
            disable_comment=False,
            disable_duet=True,
            disable_stitch=False,
            auto_add_music=True,
            chunk_size=10 * 1024**2,
            status_poll_interval=1,
            status_poll_attempts=3,
        ),
    )

    publishers = build_publishers(config)

    publisher = publishers["tiktok"]
    assert isinstance(publisher, TikTokPublisher)
    assert publisher.privacy_level == "SELF_ONLY"
    assert publisher.disable_duet
    assert publisher.tokens.client_key == "client-key"
    assert publisher.tokens.configured_refresh_token == "refresh-token"


def test_publisher_factory_registers_enabled_whatsapp() -> None:
    config = AppConfig(
        bot_token="telegram-token",
        owner_id=123,
        telegram_api=_telegram_api(),
        targets=(),
        whatsapp_engine="openwa",
        whatsapp=WhatsAppConfig(
            api_url="http://openwa:2785/api",
            api_key="w" * 32,
            session_id="session-id",
            request_timeout=90,
            media_base_url="http://media-server",
            media_root=Path("media"),
            media_max_bytes=100 * 1024**2,
        ),
        whatsapp_cloud=None,
        instagram=None,
        tiktok=None,
    )

    publishers = build_publishers(config)

    publisher = publishers["whatsapp"]
    assert isinstance(publisher, WhatsAppPublisher)
    assert publisher.session_id == "session-id"


def test_publisher_factory_registers_whatsapp_cloud_engine() -> None:
    config = AppConfig(
        bot_token="telegram-token",
        owner_id=123,
        telegram_api=_telegram_api(),
        targets=(),
        whatsapp_engine="cloud",
        whatsapp=None,
        whatsapp_cloud=WhatsAppCloudConfig(
            access_token="graph-token",
            phone_number_id="123456789",
            api_base_url="https://graph.example",
            api_version="v25.0",
            request_timeout=90,
            media_max_bytes=16 * 1024**2,
        ),
        instagram=None,
        tiktok=None,
    )

    publishers = build_publishers(config)

    publisher = publishers["whatsapp"]
    assert isinstance(publisher, WhatsAppCloudPublisher)
    assert publisher.number_url == "https://graph.example/v25.0/123456789"
