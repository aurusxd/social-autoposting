from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from app.publishers import MediaFile, Post, PublishTarget
from app.publishers.tiktok_client import MemoryTokenStore
from app.publishers.tiktok_publisher import (
    MAX_CHUNK_SIZE,
    TikTokPublisher,
    _chunk_plan,
)


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


def _ok(data: dict[str, Any]) -> FakeResponse:
    return FakeResponse(200, {"data": data, "error": {"code": "ok", "message": ""}})


class FakeSession:
    """Answers the OAuth, creator-info, init and status calls of a direct post."""

    def __init__(
        self,
        overrides: dict[str, FakeResponse] | None = None,
        privacy_levels: list[str] | None = None,
        status: str = "PUBLISH_COMPLETE",
    ) -> None:
        self.overrides = overrides or {}
        self.privacy_levels = privacy_levels
        self.status = status
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.uploads: list[tuple[str, bytes]] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        for suffix, response in self.overrides.items():
            if url.endswith(suffix):
                return response
        if url.endswith("/v2/oauth/token/"):
            return FakeResponse(
                200,
                {
                    "access_token": "access-token",
                    "expires_in": 86400,
                    "refresh_token": "rotated-refresh-token",
                    "refresh_expires_in": 31536000,
                },
            )
        if url.endswith("/creator_info/query/"):
            creator: dict[str, Any] = {"creator_username": "tester"}
            if self.privacy_levels is not None:
                creator["privacy_level_options"] = self.privacy_levels
            return _ok(creator)
        if url.endswith("/video/init/"):
            return _ok(
                {
                    "publish_id": "publish-id",
                    "upload_url": "https://upload.tiktok.example/chunk",
                }
            )
        if url.endswith("/content/init/"):
            return _ok({"publish_id": "publish-id"})
        if url.endswith("/status/fetch/"):
            return _ok({"status": self.status, "fail_reason": "spam_risk"})
        raise AssertionError(f"unexpected POST {url}")

    async def put(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("PUT", url, kwargs))
        self.uploads.append((kwargs["headers"]["Content-Range"], kwargs["data"]))
        return FakeResponse(200)

    def body(self, suffix: str) -> dict[str, Any]:
        for _, url, kwargs in self.calls:
            if url.endswith(suffix):
                return kwargs["json"]
        raise AssertionError(f"no call ending with {suffix}")

    def form(self, suffix: str) -> dict[str, Any]:
        for _, url, kwargs in self.calls:
            if url.endswith(suffix):
                return kwargs["data"]
        raise AssertionError(f"no call ending with {suffix}")


def _publisher(
    tmp_path: Path,
    session: FakeSession,
    store: MemoryTokenStore | None = None,
    **overrides: Any,
) -> TikTokPublisher:
    def session_factory(**kwargs: Any) -> FakeSession:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        return session

    async def sleep(_: float) -> None:
        return None

    publisher = TikTokPublisher(
        client_key="client-key",
        client_secret="client-secret",
        refresh_token="refresh-token",
        media_base_url="https://media.example",
        media_root=tmp_path,
        api_base_url="https://open.tiktokapis.example",
        request_timeout=90,
        status_poll_interval=0,
        status_poll_attempts=2,
        token_store=store or MemoryTokenStore(),
        session_factory=session_factory,
        sleep=sleep,
        **overrides,
    )
    publisher.media_root = tmp_path.resolve()
    return publisher


def _video(tmp_path: Path, size: int = 1024, name: str = "clip.mp4") -> MediaFile:
    path = tmp_path / name
    path.write_bytes(b"v" * size)
    return MediaFile(file_path=str(path), media_type="video")


def _photo(tmp_path: Path, name: str = "photo.jpg", position: int = 0) -> MediaFile:
    path = tmp_path / name
    path.write_bytes(b"p" * 64)
    return MediaFile(file_path=str(path), media_type="photo", position=position)


def _feed() -> PublishTarget:
    return PublishTarget("self", "feed", "TikTok")


def test_video_is_uploaded_and_direct_posted(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(tmp_path, session)
    post = Post(id=1, caption="Видео", media_files=(_video(tmp_path),))

    result = asyncio.run(publisher.publish(post, _feed()))

    assert result.success
    assert result.external_id == "publish-id"
    body = session.body("/video/init/")
    assert body["post_info"]["title"] == "Видео"
    assert body["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert body["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": 1024,
        "chunk_size": 1024,
        "total_chunk_count": 1,
    }
    assert session.uploads == [("bytes 0-1023/1024", b"v" * 1024)]


def test_photo_post_pulls_public_urls(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(tmp_path, session)
    post = Post(
        id=2,
        caption="Фото",
        media_files=(
            _photo(tmp_path, "one.jpg", 0),
            _photo(tmp_path, "two.jpg", 1),
        ),
    )

    result = asyncio.run(publisher.publish(post, _feed()))

    assert result.success
    body = session.body("/content/init/")
    assert body["media_type"] == "PHOTO"
    assert body["post_mode"] == "DIRECT_POST"
    assert body["source_info"]["photo_images"] == [
        "https://media.example/one.jpg",
        "https://media.example/two.jpg",
    ]
    assert body["post_info"]["auto_add_music"] is True
    assert not session.uploads


def test_access_token_is_refreshed_and_rotation_is_stored(tmp_path: Path) -> None:
    session = FakeSession()
    store = MemoryTokenStore()
    publisher = _publisher(tmp_path, session, store)
    post = Post(id=3, caption="", media_files=(_video(tmp_path),))

    assert asyncio.run(publisher.publish(post, _feed())).success

    assert session.form("/v2/oauth/token/")["grant_type"] == "refresh_token"
    stored = store.load("tiktok")
    assert stored is not None
    assert stored.access_token == "access-token"
    assert stored.refresh_token == "rotated-refresh-token"
    assert stored.is_fresh()


def test_cached_token_skips_the_oauth_call(tmp_path: Path) -> None:
    session = FakeSession()
    store = MemoryTokenStore()
    publisher = _publisher(tmp_path, session, store)
    post = Post(id=4, caption="", media_files=(_video(tmp_path),))

    assert asyncio.run(publisher.publish(post, _feed())).success
    session.calls.clear()
    assert asyncio.run(publisher.publish(post, _feed())).success

    assert not [url for _, url, _ in session.calls if url.endswith("/v2/oauth/token/")]


def test_failed_publication_is_not_retryable(tmp_path: Path) -> None:
    session = FakeSession(status="FAILED")
    publisher = _publisher(tmp_path, session)
    post = Post(id=5, caption="", media_files=(_video(tmp_path),))

    result = asyncio.run(publisher.publish(post, _feed()))

    assert not result.success
    assert not result.retryable
    assert "spam_risk" in (result.error or "")


def test_rate_limited_init_is_retryable(tmp_path: Path) -> None:
    session = FakeSession(
        overrides={
            "/video/init/": FakeResponse(
                429,
                {"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
                headers={"Retry-After": "30"},
            )
        }
    )
    publisher = _publisher(tmp_path, session)
    post = Post(id=6, caption="", media_files=(_video(tmp_path),))

    result = asyncio.run(publisher.publish(post, _feed()))

    assert not result.success
    assert result.retryable
    assert result.retry_after == 30


def test_unsupported_privacy_level_fails_before_upload(tmp_path: Path) -> None:
    session = FakeSession(privacy_levels=["SELF_ONLY", "FOLLOWER_OF_CREATOR"])
    publisher = _publisher(tmp_path, session)
    post = Post(id=7, caption="", media_files=(_video(tmp_path),))

    result = asyncio.run(publisher.publish(post, _feed()))

    assert not result.success
    assert not result.retryable
    assert "PUBLIC_TO_EVERYONE" in (result.error or "")
    assert not session.uploads


def test_account_level_interaction_locks_are_honoured(tmp_path: Path) -> None:
    class LockedSession(FakeSession):
        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            if url.endswith("/creator_info/query/"):
                self.calls.append(("POST", url, kwargs))
                return _ok({"duet_disabled": True, "comment_disabled": True})
            return await super().post(url, **kwargs)

    session = LockedSession()
    publisher = _publisher(tmp_path, session)
    post = Post(id=8, caption="", media_files=(_video(tmp_path),))

    assert asyncio.run(publisher.publish(post, _feed())).success

    post_info = session.body("/video/init/")["post_info"]
    assert post_info["disable_duet"] is True
    assert post_info["disable_comment"] is True
    assert post_info["disable_stitch"] is False


def test_mixed_media_is_rejected(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(tmp_path, session)
    post = Post(
        id=9,
        caption="",
        media_files=(_photo(tmp_path), _video(tmp_path)),
    )

    result = asyncio.run(publisher.publish(post, _feed()))

    assert not result.success
    assert not result.retryable
    assert "mix" in (result.error or "")


def test_chunk_plan_uses_one_chunk_for_small_files() -> None:
    assert _chunk_plan(1024, 10 * 1024**2) == (1024, 1)
    assert _chunk_plan(MAX_CHUNK_SIZE, 10 * 1024**2) == (MAX_CHUNK_SIZE, 1)


def test_chunk_plan_splits_large_files_and_keeps_the_remainder_last() -> None:
    chunk_size, total = _chunk_plan(250 * 1024**2, 10 * 1024**2)

    assert chunk_size == 10 * 1024**2
    assert total == 25


def test_chunk_plan_respects_the_api_limits_at_the_maximum_file_size() -> None:
    # 4 GB is the largest video TikTok accepts; the plan must stay inside
    # the 5 MB..64 MB chunk window and the 1000 chunk ceiling.
    chunk_size, total = _chunk_plan(4 * 1024**3, 1024)

    assert 5 * 1024**2 <= chunk_size <= MAX_CHUNK_SIZE
    assert 0 < total <= 1000


def test_upload_sends_each_chunk_with_its_own_content_range(tmp_path: Path) -> None:
    session = FakeSession()
    publisher = _publisher(tmp_path, session)
    path = tmp_path / "big.mp4"
    payload = bytes(range(256)) * 40  # 10240 bytes
    path.write_bytes(payload)

    asyncio.run(
        publisher._upload_video(
            session,
            "https://upload.tiktok.example/chunk",
            path=path,
            size=len(payload),
            chunk_size=4096,
            total_chunks=2,
            content_type="video/mp4",
        )
    )

    ranges = [content_range for content_range, _ in session.uploads]
    assert ranges == ["bytes 0-4095/10240", "bytes 4096-10239/10240"]
    assert b"".join(chunk for _, chunk in session.uploads) == payload
