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
    """Serves the container/status/publish trio of the Graph publishing flow."""

    def __init__(
        self,
        container_response: FakeResponse | None = None,
        publish_response: FakeResponse | None = None,
        status_code: str = "FINISHED",
    ) -> None:
        self.container_response = container_response
        self.publish_response = publish_response or FakeResponse(
            200,
            {"id": "media-id"},
        )
        self.status_code = status_code
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._containers = 0

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
        if url.endswith("/media_publish"):
            return self.publish_response
        if url.endswith("/media"):
            if self.container_response is not None:
                return self.container_response
            self._containers += 1
            return FakeResponse(200, {"id": f"container-{self._containers}"})
        return FakeResponse(200, {"status_code": self.status_code, "status": "detail"})

    @property
    def container_payloads(self) -> list[dict[str, Any]]:
        return [
            kwargs["data"]
            for _, url, kwargs in self.calls
            if url.endswith("/media") and "data" in kwargs
        ]


def _publisher(session: FakeSession) -> InstagramPublisher:
    def session_factory(**kwargs: Any) -> FakeSession:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        return session

    async def sleep(_: float) -> None:
        return None

    return InstagramPublisher(
        access_token="graph-token",
        ig_user_id="17841400000000000",
        media_base_url="https://media.example",
        media_root=Path("media"),
        api_base_url="https://graph.example",
        api_version="v25.0",
        request_timeout=90,
        status_poll_interval=0,
        status_poll_attempts=3,
        session_factory=session_factory,
        sleep=sleep,
    )


def _photo(tmp_path: Path, name: str = "photo.jpg") -> MediaFile:
    path = tmp_path / name
    path.write_bytes(b"\xff\xd8\xff" + b"0" * 32)
    return MediaFile(file_path=str(path), media_type="photo")


def _video(tmp_path: Path, name: str = "clip.mp4") -> MediaFile:
    path = tmp_path / name
    path.write_bytes(b"0" * 128)
    return MediaFile(file_path=str(path), media_type="video")


def _publisher_for(tmp_path: Path, session: FakeSession) -> InstagramPublisher:
    publisher = _publisher(session)
    publisher.media_root = tmp_path.resolve()
    return publisher


def test_single_photo_creates_image_container_and_publishes(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher_for(tmp_path, session)
    post = Post(id=7, caption="Подпись", media_files=(_photo(tmp_path),))

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "feed", "Instagram"))
    )

    assert result.success
    assert result.external_id == "media-id"
    payload = session.container_payloads[0]
    assert payload["media_type"] == "IMAGE"
    assert payload["image_url"] == "https://media.example/photo.jpg"
    assert payload["caption"] == "Подпись"
    publish_calls = [
        url for _, url, _ in session.calls if url.endswith("media_publish")
    ]
    assert publish_calls == [
        "https://graph.example/v25.0/17841400000000000/media_publish"
    ]


def test_single_video_is_published_as_reel(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher_for(tmp_path, session)
    post = Post(id=8, caption="Reel", media_files=(_video(tmp_path),))

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "feed", "Instagram"))
    )

    assert result.success
    payload = session.container_payloads[0]
    assert payload["media_type"] == "REELS"
    assert payload["video_url"] == "https://media.example/clip.mp4"
    assert payload["share_to_feed"] == "true"


def test_story_container_carries_no_caption(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher_for(tmp_path, session)
    post = Post(id=9, caption="Не для истории", media_files=(_photo(tmp_path),))

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "story", "История"))
    )

    assert result.success
    payload = session.container_payloads[0]
    assert payload["media_type"] == "STORIES"
    assert "caption" not in payload


def test_carousel_builds_children_then_parent(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher_for(tmp_path, session)
    post = Post(
        id=10,
        caption="Карусель",
        media_files=(
            MediaFile(str(_photo(tmp_path, "one.jpg").file_path), "photo", position=0),
            MediaFile(str(_video(tmp_path, "two.mp4").file_path), "video", position=1),
        ),
    )

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "feed", "Instagram"))
    )

    assert result.success
    payloads = session.container_payloads
    assert payloads[0]["is_carousel_item"] == "true"
    assert payloads[0]["media_type"] == "IMAGE"
    # A carousel video item must be VIDEO, never REELS.
    assert payloads[1]["media_type"] == "VIDEO"
    assert payloads[2] == {
        "media_type": "CAROUSEL",
        "children": "container-1,container-2",
        "caption": "Карусель",
    }


def test_container_error_status_is_not_retryable(tmp_path: Path) -> None:
    session = FakeSession(status_code="ERROR")
    publisher = _publisher_for(tmp_path, session)
    post = Post(id=11, caption="", media_files=(_video(tmp_path),))

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "feed", "Instagram"))
    )

    assert not result.success
    assert not result.retryable
    assert "ERROR" in (result.error or "")


def test_rate_limited_graph_error_is_retryable(tmp_path: Path) -> None:
    session = FakeSession(
        container_response=FakeResponse(
            400,
            {"error": {"message": "limit reached", "code": 4}},
            headers={"Retry-After": "42"},
        )
    )
    publisher = _publisher_for(tmp_path, session)
    post = Post(id=12, caption="", media_files=(_photo(tmp_path),))

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "feed", "Instagram"))
    )

    assert not result.success
    assert result.retryable
    assert result.retry_after == 42


def test_permission_graph_error_is_not_retryable(tmp_path: Path) -> None:
    session = FakeSession(
        container_response=FakeResponse(
            400,
            {"error": {"message": "no permission", "code": 200}},
        )
    )
    publisher = _publisher_for(tmp_path, session)
    post = Post(id=13, caption="", media_files=(_photo(tmp_path),))

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "feed", "Instagram"))
    )

    assert not result.success
    assert not result.retryable


def test_media_outside_media_root_is_rejected(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher_for(tmp_path / "inside", session)
    (tmp_path / "inside").mkdir()
    post = Post(id=14, caption="", media_files=(_photo(tmp_path),))

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "feed", "Instagram"))
    )

    assert not result.success
    assert not result.retryable
    assert "media root" in (result.error or "")


def test_png_photo_is_rejected(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher_for(tmp_path, session)
    post = Post(id=15, caption="", media_files=(_photo(tmp_path, "photo.png"),))

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "feed", "Instagram"))
    )

    assert not result.success
    assert "JPEG" in (result.error or "")


def test_story_requires_exactly_one_file(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher_for(tmp_path, session)
    post = Post(
        id=16,
        caption="",
        media_files=(
            MediaFile(str(_photo(tmp_path, "a.jpg").file_path), "photo", position=0),
            MediaFile(str(_photo(tmp_path, "b.jpg").file_path), "photo", position=1),
        ),
    )

    result = asyncio.run(
        publisher.publish(post, PublishTarget("self", "story", "История"))
    )

    assert not result.success
    assert not result.retryable
