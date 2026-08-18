from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from app.publishers import MediaFile, Post, PublishTarget
from app.publishers.instagram_publisher import InstagramPublisher


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
    def __init__(self, create_response: FakeResponse | None = None) -> None:
        self.create_response = create_response or FakeResponse(
            201,
            {"post": {"_id": "instagram-post-id"}},
        )
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

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
            filename = kwargs["json"]["filename"]
            return FakeResponse(
                200,
                {
                    "uploadUrl": f"https://storage.example/{filename}",
                    "publicUrl": f"https://media.example/{filename}",
                },
            )
        return self.create_response

    async def put(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("PUT", url, kwargs))
        kwargs["data"].read()
        return FakeResponse(200)


def _publisher(session: FakeSession) -> InstagramPublisher:
    def session_factory(**kwargs: Any) -> FakeSession:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        return session

    return InstagramPublisher(
        api_key="secret-key",
        account_id="instagram-account",
        api_base_url="https://zernio.example/api",
        request_timeout=90,
        session_factory=session_factory,
    )


def _media(path: Path, media_type: str, position: int = 0) -> MediaFile:
    path.write_bytes(b"test-media")
    return MediaFile(str(path), media_type, position=position)


def _publish(session: FakeSession, post: Post, target_kind: str):
    return asyncio.run(
        _publisher(session).publish(
            post,
            PublishTarget("self", target_kind, "Instagram"),
        )
    )


def test_feed_carousel_is_uploaded_in_position_order(tmp_path: Path) -> None:
    session = FakeSession()
    second = _media(tmp_path / "second.mp4", "video", position=1)
    first = _media(tmp_path / "first.jpg", "photo", position=0)

    result = _publish(
        session,
        Post(id=1, caption="Подпись", media_files=(second, first)),
        "feed",
    )

    payload = session.calls[-1][2]["json"]
    assert result.success
    assert result.external_id == "instagram-post-id"
    assert payload["content"] == "Подпись"
    assert payload["publishNow"] is True
    assert payload["mediaItems"] == [
        {"url": "https://media.example/first.jpg", "type": "image"},
        {"url": "https://media.example/second.mp4", "type": "video"},
    ]
    assert payload["platforms"] == [
        {"platform": "instagram", "accountId": "instagram-account"}
    ]
    assert session.calls[-1][2]["headers"]["x-request-id"]


def test_story_uses_story_content_type_and_omits_caption(tmp_path: Path) -> None:
    session = FakeSession()
    photo = _media(tmp_path / "story.png", "photo")

    result = _publish(
        session,
        Post(id=2, caption="Story caption", media_files=(photo,)),
        "story",
    )

    payload = session.calls[-1][2]["json"]
    assert result.success
    assert "content" not in payload
    assert payload["platforms"][0]["platformSpecificData"] == {"contentType": "story"}


def test_single_feed_video_is_shared_as_reel(tmp_path: Path) -> None:
    session = FakeSession()
    video = _media(tmp_path / "reel.mov", "video")

    result = _publish(
        session,
        Post(id=3, caption="Reel", media_files=(video,)),
        "feed",
    )

    assert result.success
    payload = session.calls[-1][2]["json"]
    assert payload["platforms"][0]["platformSpecificData"] == {"shareToFeed": True}


def test_rate_limit_is_retryable_and_uses_retry_after(tmp_path: Path) -> None:
    session = FakeSession(
        FakeResponse(
            429,
            {"error": "rate limit"},
            headers={"Retry-After": "75"},
        )
    )
    photo = _media(tmp_path / "photo.jpg", "photo")

    result = _publish(
        session,
        Post(id=4, caption=None, media_files=(photo,)),
        "feed",
    )

    assert not result.success
    assert result.retryable
    assert result.retry_after == 75
    assert "rate limit" in (result.error or "")


def test_story_with_multiple_files_is_rejected_before_http(tmp_path: Path) -> None:
    session = FakeSession()
    first = _media(tmp_path / "first.jpg", "photo")
    second = _media(tmp_path / "second.jpg", "photo", position=1)

    result = _publish(
        session,
        Post(id=5, caption=None, media_files=(first, second)),
        "story",
    )

    assert not result.success
    assert not result.retryable
    assert session.calls == []
