from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from app.publishers import MediaFile, Post, PublishTarget
from app.publishers.whatsapp_publisher import WhatsAppPublisher

TOKEN = "whapi-secret-token"


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

    async def text(self) -> str:
        return self._text

    def release(self) -> None:
        return None


class FakeSession:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._sent = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if self.response is not None:
            return self.response
        self._sent += 1
        return FakeResponse(200, {"message": {"id": f"wamid.{self._sent}"}})

    @property
    def urls(self) -> list[str]:
        return [url for _, url, _ in self.calls]

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [kwargs["json"] for _, _, kwargs in self.calls if "json" in kwargs]


def _publisher(session: FakeSession, **overrides: Any) -> WhatsAppPublisher:
    def session_factory(**kwargs: Any) -> FakeSession:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        return session

    return WhatsAppPublisher(
        api_token=TOKEN,
        api_url="https://gate.whapi.example",
        request_timeout=90,
        session_factory=session_factory,
        **overrides,
    )


def _photo(tmp_path: Path, name: str = "photo.jpg", position: int = 0) -> MediaFile:
    path = tmp_path / name
    path.write_bytes(b"\xff\xd8\xff" + b"0" * 64)
    return MediaFile(file_path=str(path), media_type="photo", position=position)


def _group() -> PublishTarget:
    return PublishTarget("120363000000000000@g.us", "group", "Группа клиентов")


def _channel() -> PublishTarget:
    return PublishTarget("120363171744447809@newsletter", "channel", "Канал")


def test_text_only_post_goes_to_a_group() -> None:
    session = FakeSession()
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=1, caption="Привет"), _group()))

    assert result.success
    assert result.external_id == "wamid.1"
    assert session.urls == ["https://gate.whapi.example/messages/text"]
    assert session.bodies == [{"to": "120363000000000000@g.us", "body": "Привет"}]


def test_channel_target_is_supported() -> None:
    session = FakeSession()
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=2, caption="Пост"), _channel()))

    assert result.success
    assert session.bodies[0]["to"] == "120363171744447809@newsletter"


def test_bearer_token_is_sent_in_the_header() -> None:
    session = FakeSession()
    publisher = _publisher(session)

    asyncio.run(publisher.publish(Post(id=3, caption="Привет"), _group()))

    _, _, kwargs = session.calls[0]
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_photo_is_uploaded_as_multipart(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session)
    post = Post(id=4, caption="Подпись", media_files=(_photo(tmp_path),))

    result = asyncio.run(publisher.publish(post, _group()))

    assert result.success
    assert session.urls == ["https://gate.whapi.example/messages/image"]
    _, _, kwargs = session.calls[0]
    assert isinstance(kwargs["data"], aiohttp.FormData)
    assert "json" not in kwargs


def test_video_uses_the_video_endpoint(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"0" * 128)
    post = Post(
        id=5,
        caption="",
        media_files=(MediaFile(file_path=str(path), media_type="video"),),
    )

    result = asyncio.run(publisher.publish(post, _group()))

    assert result.success
    assert session.urls == ["https://gate.whapi.example/messages/video"]


def test_long_caption_is_sent_as_a_separate_text_message(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session)
    post = Post(id=6, caption="д" * 1500, media_files=(_photo(tmp_path),))

    result = asyncio.run(publisher.publish(post, _group()))

    assert result.success
    assert session.urls == [
        "https://gate.whapi.example/messages/text",
        "https://gate.whapi.example/messages/image",
    ]


def test_only_the_first_media_carries_the_caption(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session)
    post = Post(
        id=7,
        caption="Подпись",
        media_files=(
            _photo(tmp_path, "one.jpg", 0),
            _photo(tmp_path, "two.jpg", 1),
        ),
    )

    result = asyncio.run(publisher.publish(post, _group()))

    assert result.success
    assert result.external_id == "wamid.1,wamid.2"
    assert len(session.calls) == 2


def test_rate_limited_send_is_retryable() -> None:
    session = FakeSession(
        FakeResponse(
            429,
            {"error": {"message": "too many requests"}},
            headers={"Retry-After": "17"},
        )
    )
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=8, caption="Привет"), _group()))

    assert not result.success
    assert result.retryable
    assert result.retry_after == 17


def test_unauthorized_send_is_not_retryable() -> None:
    session = FakeSession(FakeResponse(401, {"error": {"message": "bad token"}}))
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=9, caption="Привет"), _group()))

    assert not result.success
    assert not result.retryable


def test_token_never_leaks_into_the_error_text() -> None:
    session = FakeSession(
        FakeResponse(500, None, text=f"upstream failed for token {TOKEN}")
    )
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=10, caption="Привет"), _group()))

    assert not result.success
    # The error is stored in the database and shown in Telegram.
    assert TOKEN not in (result.error or "")


def test_partial_delivery_disables_the_retry(tmp_path: Path) -> None:
    class FlakySession(FakeSession):
        async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            if self._sent >= 1:
                self.calls.append((method, url, kwargs))
                return FakeResponse(500, {"error": {"message": "boom"}})
            return await super().request(method, url, **kwargs)

    session = FlakySession()
    publisher = _publisher(session)
    post = Post(
        id=11,
        caption="Подпись",
        media_files=(
            _photo(tmp_path, "one.jpg", 0),
            _photo(tmp_path, "two.jpg", 1),
        ),
    )

    result = asyncio.run(publisher.publish(post, _group()))

    assert not result.success
    # The first image already reached the chat; retrying would duplicate it.
    assert not result.retryable
    assert "Partial WhatsApp publication" in (result.error or "")


def test_oversized_media_is_rejected_before_any_request(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(session, media_max_bytes=1024)
    path = tmp_path / "huge.jpg"
    path.write_bytes(b"0" * 4096)
    post = Post(
        id=12,
        caption="",
        media_files=(MediaFile(file_path=str(path), media_type="photo"),),
    )

    result = asyncio.run(publisher.publish(post, _group()))

    assert not result.success
    assert not result.retryable
    assert not session.calls


def test_unsupported_target_kind_is_rejected() -> None:
    session = FakeSession()
    publisher = _publisher(session)
    target = PublishTarget("79001234567@c.us", "contact", "Клиент")

    result = asyncio.run(publisher.publish(Post(id=13, caption="Привет"), target))

    assert not result.success
    assert not result.retryable
    assert not session.calls


def test_empty_post_is_rejected() -> None:
    session = FakeSession()
    publisher = _publisher(session)

    result = asyncio.run(publisher.publish(Post(id=14, caption=None), _group()))

    assert not result.success
    assert not session.calls
