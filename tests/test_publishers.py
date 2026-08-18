import asyncio
from pathlib import Path

from app.core.config import (
    AppConfig,
    InstagramConfig,
    TelegramAPIConfig,
    TikTokConfig,
)
from app.publishers import Post, PublishResult, PublishTarget
from app.publishers.factory import build_publishers
from app.publishers.fakes import FakePublisher
from app.publishers.instagram_publisher import InstagramPublisher
from app.publishers.tiktok_publisher import TikTokPublisher


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
        instagram=InstagramConfig(
            api_key="zernio-key",
            account_id="instagram-account",
            api_base_url="https://zernio.example/api",
            request_timeout=90,
        ),
        tiktok=None,
    )

    publishers = build_publishers(config)

    assert isinstance(publishers["instagram"], InstagramPublisher)
    assert publishers["instagram"].account_id == "instagram-account"


def test_publisher_factory_registers_enabled_tiktok() -> None:
    config = AppConfig(
        bot_token="telegram-token",
        owner_id=123,
        telegram_api=_telegram_api(),
        targets=(),
        instagram=None,
        tiktok=TikTokConfig(
            api_key="zernio-key",
            account_id="tiktok-account",
            api_base_url="https://zernio.example/api",
            request_timeout=90,
            privacy_level="SELF_ONLY",
        ),
    )

    publishers = build_publishers(config)

    publisher = publishers["tiktok"]
    assert isinstance(publisher, TikTokPublisher)
    assert publisher.account_id == "tiktok-account"
    assert publisher.privacy_level == "SELF_ONLY"
