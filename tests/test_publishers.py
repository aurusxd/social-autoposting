import asyncio
from pathlib import Path

from app.core.config import AppConfig, InstagramConfig
from app.publishers import Post, PublishResult, PublishTarget
from app.publishers.factory import build_publishers
from app.publishers.fakes import FakePublisher
from app.publishers.instagram_publisher import InstagramPublisher


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
        targets=(),
        instagram=InstagramConfig(
            username="instagram-user",
            password="instagram-password",
            totp_secret=None,
            session_path=Path("data/instagram-session.json"),
            proxy=None,
            request_timeout=30,
        ),
    )

    publishers = build_publishers(config)

    assert isinstance(publishers["instagram"], InstagramPublisher)
