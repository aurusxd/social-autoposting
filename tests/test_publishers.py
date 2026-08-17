import asyncio

from app.publishers import Post, PublishResult, PublishTarget
from app.publishers.fakes import FakePublisher


def test_fake_publisher_records_calls_and_result() -> None:
    expected = PublishResult(success=False, retryable=True, error="timeout")
    publisher = FakePublisher(platform="telegram", result=expected)
    post = Post(id=1, caption="Тест")
    target = PublishTarget(key="-1001", kind="channel", name="Основной")

    result = asyncio.run(publisher.publish(post, target))

    assert result == expected
    assert publisher.calls == [(post, target)]
