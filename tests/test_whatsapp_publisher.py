from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import aiohttp

from app.publishers import MediaFile, Post, PublishTarget
from app.publishers.whatsapp_publisher import WhatsAppPublisher


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
    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.responses:
            return self.responses.pop(0)
        message_number = len(self.calls)
        return FakeResponse(201, {"messageId": f"message-{message_number}"})


def _publisher(
    session: FakeSession,
    media_root: Path,
    media_base_url: str | None = "http://media-server",
) -> WhatsAppPublisher:
    def session_factory(**kwargs: Any) -> FakeSession:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        return session

    return WhatsAppPublisher(
        api_url="http://openwa:2785/api",
        api_key="secret-key",
        session_id="session/id",
        media_base_url=media_base_url,
        media_root=media_root,
        session_factory=session_factory,
    )


def _publish(
    publisher: WhatsAppPublisher,
    post: Post,
    *,
    key: str = "120363123456789@g.us",
    kind: str = "group",
):
    return asyncio.run(publisher.publish(post, PublishTarget(key, kind, "WhatsApp")))


def _media(path: Path, media_type: str, position: int = 0) -> MediaFile:
    path.write_bytes(f"{media_type}-bytes".encode())
    return MediaFile(str(path), media_type, position=position)


def test_text_is_sent_to_selected_group(tmp_path: Path) -> None:
    session = FakeSession()

    result = _publish(
        _publisher(session, tmp_path),
        Post(id=1, caption="Текст для группы"),
    )

    assert result.success
    assert result.external_id == "message-1"
    url, request = session.calls[0]
    assert url.endswith("/sessions/session%2Fid/messages/send-text")
    assert request["json"] == {
        "chatId": "120363123456789@g.us",
        "text": "Текст для группы",
    }
    assert request["headers"] == {"X-API-Key": "secret-key"}


def test_media_uses_internal_urls_and_preserves_order(tmp_path: Path) -> None:
    session = FakeSession()
    second = _media(tmp_path / "second.mp4", "video", position=1)
    first = _media(tmp_path / "first photo.jpg", "photo", position=0)

    result = _publish(
        _publisher(session, tmp_path),
        Post(id=2, caption="Подпись", media_files=(second, first)),
        key="120363000000000000@newsletter",
        kind="channel",
    )

    assert result.success
    assert result.external_id == "message-1,message-2"
    assert session.calls[0][0].endswith("/messages/send-image")
    assert session.calls[0][1]["json"] == {
        "chatId": "120363000000000000@newsletter",
        "caption": "Подпись",
        "filename": "first photo.jpg",
        "mimetype": "image/jpeg",
        "url": "http://media-server/first%20photo.jpg",
    }
    assert session.calls[1][0].endswith("/messages/send-video")
    assert session.calls[1][1]["json"]["url"].endswith("/second.mp4")
    assert "caption" not in session.calls[1][1]["json"]


def test_media_falls_back_to_base64_without_media_server(tmp_path: Path) -> None:
    session = FakeSession()
    photo = _media(tmp_path / "photo.png", "photo")

    result = _publish(
        _publisher(session, tmp_path, media_base_url=None),
        Post(id=3, caption=None, media_files=(photo,)),
    )

    assert result.success
    payload = session.calls[0][1]["json"]
    assert payload["mimetype"] == "image/png"
    assert base64.b64decode(payload["base64"]) == b"photo-bytes"
    assert payload["filename"] == "photo.png"
    assert "url" not in payload


def test_media_falls_back_to_base64_when_openwa_rejects_url(
    tmp_path: Path,
) -> None:
    session = FakeSession(
        [
            FakeResponse(400, {"message": "Bad Request"}),
            FakeResponse(201, {"messageId": "fallback-message"}),
        ]
    )
    photo = _media(tmp_path / "photo.jpg", "photo")

    result = _publish(
        _publisher(session, tmp_path),
        Post(id=7, caption="Подпись", media_files=(photo,)),
    )

    assert result.success
    assert result.external_id == "fallback-message"
    first_payload = session.calls[0][1]["json"]
    fallback_payload = session.calls[1][1]["json"]
    assert first_payload["url"] == "http://media-server/photo.jpg"
    assert "base64" not in first_payload
    assert "url" not in fallback_payload
    assert fallback_payload["mimetype"] == "image/jpeg"
    assert fallback_payload["filename"] == "photo.jpg"
    assert base64.b64decode(fallback_payload["base64"]) == b"photo-bytes"


def test_long_caption_is_sent_as_text_before_media(tmp_path: Path) -> None:
    session = FakeSession()
    photo = _media(tmp_path / "photo.jpg", "photo")
    caption = "a" * 1025

    result = _publish(
        _publisher(session, tmp_path),
        Post(id=4, caption=caption, media_files=(photo,)),
    )

    assert result.success
    assert session.calls[0][0].endswith("/messages/send-text")
    assert session.calls[0][1]["json"]["text"] == caption
    assert session.calls[1][0].endswith("/messages/send-image")
    assert "caption" not in session.calls[1][1]["json"]


def test_rate_limit_is_retryable_and_honors_retry_after(tmp_path: Path) -> None:
    session = FakeSession(
        [FakeResponse(429, {"message": "slow down", "retryAfterSeconds": 45})]
    )

    result = _publish(
        _publisher(session, tmp_path),
        Post(id=5, caption="Message"),
    )

    assert not result.success
    assert result.retryable
    assert result.retry_after == 45
    assert "slow down" in (result.error or "")


def test_partial_publication_is_not_retried_to_avoid_duplicates(tmp_path: Path) -> None:
    session = FakeSession(
        [
            FakeResponse(201, {"messageId": "text-message"}),
            FakeResponse(503, {"message": "temporarily unavailable"}),
        ]
    )
    photo = _media(tmp_path / "photo.jpg", "photo")

    result = _publish(
        _publisher(session, tmp_path),
        Post(id=6, caption="a" * 1025, media_files=(photo,)),
    )

    assert not result.success
    assert not result.retryable
    assert "Partial WhatsApp publication" in (result.error or "")
