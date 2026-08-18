from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from app.publishers import MediaFile, Post, PublishTarget
from app.publishers.tiktok_publisher import TikTokPublisher


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
        self.released = False

    async def json(self, **_: Any) -> dict[str, Any] | None:
        return self.payload

    async def read(self) -> bytes:
        return self._text.encode()

    async def text(self) -> str:
        return self._text

    def release(self) -> None:
        self.released = True


class FakeSession:
    def __init__(
        self,
        create_response: FakeResponse | None = None,
    ) -> None:
        self.create_response = create_response or FakeResponse(
            201,
            {"post": {"_id": "zernio-post-id"}},
        )
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.uploaded = b""

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if url.endswith("/v1/media/presign"):
            return FakeResponse(
                200,
                {
                    "uploadUrl": "https://storage.example/upload",
                    "publicUrl": "https://media.example/video.mp4",
                },
            )
        return self.create_response

    async def put(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("PUT", url, kwargs))
        self.uploaded = kwargs["data"].read()
        return FakeResponse(200)


def _publisher(session: FakeSession) -> TikTokPublisher:
    def session_factory(**kwargs: Any) -> FakeSession:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        assert "headers" not in kwargs
        return session

    return TikTokPublisher(
        api_key="secret-key",
        account_id="tiktok-account",
        api_base_url="https://zernio.example/api",
        session_factory=session_factory,
    )


def _video(path: Path) -> MediaFile:
    path.write_bytes(b"video-bytes")
    return MediaFile(str(path), "video")


def test_video_is_uploaded_and_published_immediately(tmp_path: Path) -> None:
    session = FakeSession()
    post = Post(
        id=42,
        caption="Видео для TikTok",
        media_files=(_video(tmp_path / "video.mp4"),),
    )

    result = asyncio.run(
        _publisher(session).publish(
            post,
            PublishTarget("self", "feed", "TikTok"),
        )
    )

    assert result.success
    assert result.external_id == "zernio-post-id"
    assert session.uploaded == b"video-bytes"
    presign_call, upload_call, post_call = session.calls
    assert presign_call[2]["headers"] == {"Authorization": "Bearer secret-key"}
    assert upload_call[2]["headers"] == {"Content-Type": "video/mp4"}
    assert "Authorization" not in upload_call[2]["headers"]
    payload = post_call[2]["json"]
    assert payload["publishNow"] is True
    assert payload["platforms"] == [
        {"platform": "tiktok", "accountId": "tiktok-account"}
    ]
    assert payload["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert payload["tiktokSettings"]["express_consent_given"] is True
    assert post_call[2]["headers"]["Authorization"] == "Bearer secret-key"
    assert post_call[2]["headers"]["x-request-id"]


def test_photo_carousel_uses_photo_settings(tmp_path: Path) -> None:
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    session = FakeSession()
    caption = "Фотоальбом #лето"
    post = Post(
        id=43,
        caption=caption,
        media_files=(
            MediaFile(str(second_path), "photo", position=1),
            MediaFile(str(first_path), "photo", position=0),
        ),
    )

    result = asyncio.run(
        _publisher(session).publish(
            post,
            PublishTarget("self", "feed", "TikTok"),
        )
    )

    assert result.success
    payload = session.calls[-1][2]["json"]
    assert payload["content"] == caption
    assert payload["tiktokSettings"]["media_type"] == "photo"
    assert payload["tiktokSettings"]["description"] == caption
    assert payload["tiktokSettings"]["auto_add_music"] is True
    assert [item["url"] for item in payload["mediaItems"]] == [
        "https://media.example/video.mp4",
        "https://media.example/video.mp4",
    ]


def test_rate_limit_is_retryable_and_uses_retry_after(tmp_path: Path) -> None:
    session = FakeSession(
        FakeResponse(
            429,
            {"error": "rate limit"},
            headers={"Retry-After": "75"},
        )
    )
    post = Post(
        id=44,
        caption=None,
        media_files=(_video(tmp_path / "video.mp4"),),
    )

    result = asyncio.run(
        _publisher(session).publish(
            post,
            PublishTarget("self", "feed", "TikTok"),
        )
    )

    assert not result.success
    assert result.retryable
    assert result.retry_after == 75
    assert "rate limit" in (result.error or "")


def test_mixed_media_is_rejected_without_http_calls(tmp_path: Path) -> None:
    photo_path = tmp_path / "photo.jpg"
    photo_path.write_bytes(b"photo")
    session = FakeSession()
    post = Post(
        id=45,
        caption=None,
        media_files=(
            MediaFile(str(photo_path), "photo"),
            _video(tmp_path / "video.mp4"),
        ),
    )

    result = asyncio.run(
        _publisher(session).publish(
            post,
            PublishTarget("self", "feed", "TikTok"),
        )
    )

    assert not result.success
    assert not result.retryable
    assert session.calls == []
