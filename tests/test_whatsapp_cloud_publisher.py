from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from app.publishers import MediaFile, Post, PublishTarget
from app.publishers.whatsapp_cloud_publisher import WhatsAppCloudPublisher


class FakeResponse:
    def __init__(
        self,
        status: int,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        self._text = text

    async def json(self, **_: Any) -> dict[str, Any] | None:
        return self.payload

    async def read(self) -> bytes:
        return self._text.encode()

    async def text(self) -> str:
        return self._text

    def release(self) -> None:
        return None


class FakeSession:
    def __init__(
        self,
        message_response: FakeResponse | None = None,
        media_response: FakeResponse | None = None,
    ) -> None:
        self.message_response = message_response
        self.media_response = media_response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._messages = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if url.endswith("/media"):
            return self.media_response or FakeResponse(200, {"id": "media-id"})
        if self.message_response is not None:
            return self.message_response
        self._messages += 1
        return FakeResponse(
            200,
            {"messages": [{"id": f"wamid.{self._messages}"}]},
        )

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [
            kwargs["json"] for _, url, kwargs in self.calls if url.endswith("/messages")
        ]


def _publisher(session: FakeSession, **overrides: Any) -> WhatsAppCloudPublisher:
    def session_factory(**kwargs: Any) -> FakeSession:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        return session

    return WhatsAppCloudPublisher(
        access_token="graph-token",
        phone_number_id="123456789",
        api_base_url="https://graph.example",
        api_version="v25.0",
        request_timeout=90,
        session_factory=session_factory,
        **overrides,
    )


def _photo(tmp_path: Path, name: str = "photo.jpg", position: int = 0) -> MediaFile:
    path = tmp_path / name
    path.write_bytes(b"\xff\xd8\xff" + b"0" * 64)
    return MediaFile(file_path=str(path), media_type="photo", position=position)


def _group() -> PublishTarget:
    return PublishTarget("120363000000000000", "group", "Группа клиентов")


def _contact() -> PublishTarget:
    return PublishTarget("79001234567", "contact", "Клиент")


def test_text_only_post_goes_to_a_group() -> None:
    session = FakeSession()
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=1, caption="Привет"), _group()))

    assert result.success
    assert result.external_id == "wamid.1"
    assert session.messages == [
        {
            "messaging_product": "whatsapp",
            "recipient_type": "group",
            "to": "120363000000000000",
            "type": "text",
            "text": {"preview_url": True, "body": "Привет"},
        }
    ]


def test_contact_target_uses_individual_recipient_type() -> None:
    session = FakeSession()
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=2, caption="Привет"), _contact()))

    assert result.success
    assert session.messages[0]["recipient_type"] == "individual"
    assert session.messages[0]["to"] == "79001234567"


def test_channel_target_is_rejected_without_any_request() -> None:
    session = FakeSession()
    publisher = _publisher(session)
    target = PublishTarget("1234567890@newsletter", "channel", "WA-канал")

    result = asyncio.run(publisher.publish(Post(id=3, caption="Пост"), target))

    assert not result.success
    assert not result.retryable
    assert "OpenWA" in (result.error or "")
    assert not session.calls


def test_media_is_uploaded_then_referenced_by_id(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session)
    post = Post(id=4, caption="Подпись", media_files=(_photo(tmp_path),))

    result = asyncio.run(publisher.publish(post, _group()))

    assert result.success
    upload_calls = [url for _, url, _ in session.calls if url.endswith("/media")]
    assert upload_calls == ["https://graph.example/v25.0/123456789/media"]
    assert session.messages == [
        {
            "messaging_product": "whatsapp",
            "recipient_type": "group",
            "to": "120363000000000000",
            "type": "image",
            "image": {"id": "media-id", "caption": "Подпись"},
        }
    ]


def test_long_caption_is_sent_as_a_separate_text_message(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session)
    caption = "д" * 1500
    post = Post(id=5, caption=caption, media_files=(_photo(tmp_path),))

    result = asyncio.run(publisher.publish(post, _group()))

    assert result.success
    assert session.messages[0]["type"] == "text"
    assert session.messages[1]["type"] == "image"
    assert "caption" not in session.messages[1]["image"]


def test_only_the_first_media_carries_the_caption(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session)
    post = Post(
        id=6,
        caption="Подпись",
        media_files=(
            _photo(tmp_path, "one.jpg", 0),
            _photo(tmp_path, "two.jpg", 1),
        ),
    )

    result = asyncio.run(publisher.publish(post, _group()))

    assert result.success
    assert session.messages[0]["image"]["caption"] == "Подпись"
    assert "caption" not in session.messages[1]["image"]
    assert result.external_id == "wamid.1,wamid.2"


def test_rate_limited_send_is_retryable() -> None:
    session = FakeSession(
        message_response=FakeResponse(
            400,
            {"error": {"message": "rate limit hit", "code": 4}},
            headers={"Retry-After": "17"},
        )
    )
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=7, caption="Привет"), _group()))

    assert not result.success
    assert result.retryable
    assert result.retry_after == 17


def test_partial_delivery_disables_the_retry(tmp_path: Path) -> None:
    class FlakySession(FakeSession):
        async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            if url.endswith("/messages") and self._messages >= 1:
                self.calls.append((method, url, kwargs))
                return FakeResponse(
                    500,
                    {"error": {"message": "boom", "code": 2}},
                )
            return await super().request(method, url, **kwargs)

    session = FlakySession()
    publisher = _publisher(session)
    post = Post(
        id=8,
        caption="Подпись",
        media_files=(
            _photo(tmp_path, "one.jpg", 0),
            _photo(tmp_path, "two.jpg", 1),
        ),
    )

    result = asyncio.run(publisher.publish(post, _group()))

    assert not result.success
    # The first image already reached the group, so retrying would duplicate it.
    assert not result.retryable
    assert "Partial WhatsApp publication" in (result.error or "")


def test_oversized_photo_is_rejected_before_upload(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session)
    path = tmp_path / "huge.jpg"
    path.write_bytes(b"0" * (6 * 1024**2))
    post = Post(
        id=9,
        caption="",
        media_files=(MediaFile(file_path=str(path), media_type="photo"),),
    )

    result = asyncio.run(publisher.publish(post, _group()))

    assert not result.success
    assert not result.retryable
    assert "5 MB" in (result.error or "")
    assert not session.calls


def test_empty_post_is_rejected() -> None:
    session = FakeSession()
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=10, caption=None), _group()))

    assert not result.success
    assert not result.retryable
