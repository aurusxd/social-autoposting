from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from instagrapi.exceptions import (
    BadPassword,
    ClientConnectionError,
    PleaseWaitFewMinutes,
)

from app.publishers import MediaFile, Post, PublishTarget
from app.publishers.instagram_publisher import InstagramPublisher


@dataclass
class UploadedMedia:
    id: str


class FakeInstagramClient:
    def __init__(self, upload_error: Exception | None = None) -> None:
        self.upload_error = upload_error
        self.calls: list[tuple[Any, ...]] = []
        self.request_timeout = 0

    def set_proxy(self, proxy: str) -> None:
        self.calls.append(("set_proxy", proxy))

    def load_settings(self, path: Path) -> dict[str, Any]:
        self.calls.append(("load_settings", path))
        return {"uuids": {"uuid": "stable-device"}}

    def set_settings(self, settings: dict[str, Any]) -> bool:
        self.calls.append(("set_settings", settings))
        return True

    def totp_generate_code(self, seed: str) -> str:
        self.calls.append(("totp_generate_code", seed))
        return "123456"

    def login(
        self,
        username: str,
        password: str,
        verification_code: str = "",
    ) -> bool:
        self.calls.append(("login", username, password, verification_code))
        return True

    def dump_settings(self, path: Path) -> bool:
        self.calls.append(("dump_settings", path))
        path.write_text("{}", encoding="utf-8")
        return True

    def photo_upload(self, path: Path, caption: str) -> UploadedMedia:
        return self._upload("photo_upload", path, caption)

    def video_upload(self, path: Path, caption: str) -> UploadedMedia:
        return self._upload("video_upload", path, caption)

    def album_upload(self, paths: list[Path], caption: str) -> UploadedMedia:
        return self._upload("album_upload", paths, caption)

    def photo_upload_to_story(
        self,
        path: Path,
        caption: str,
        **kwargs: Any,
    ) -> UploadedMedia:
        return self._upload("photo_story", path, caption, kwargs)

    def video_upload_to_story(
        self,
        path: Path,
        caption: str,
        **kwargs: Any,
    ) -> UploadedMedia:
        return self._upload("video_story", path, caption, kwargs)

    def _upload(self, *call: Any) -> UploadedMedia:
        self.calls.append(call)
        if self.upload_error is not None:
            raise self.upload_error
        return UploadedMedia("instagram-media-id")


def _publisher(
    client: FakeInstagramClient,
    session_path: Path,
) -> InstagramPublisher:
    return InstagramPublisher(
        username="user",
        password="password",
        totp_secret="totp-secret",
        session_path=session_path,
        proxy="http://proxy:8080",
        request_timeout=45,
        client_factory=lambda: client,
    )


def _media(path: Path, media_type: str, position: int = 0) -> MediaFile:
    path.write_bytes(b"test-media")
    return MediaFile(str(path), media_type, position=position)


def test_feed_album_uploads_media_in_position_order(tmp_path: Path) -> None:
    client = FakeInstagramClient()
    session_path = tmp_path / "instagram-session.json"
    session_path.write_text("{}", encoding="utf-8")
    second = _media(tmp_path / "second.mp4", "video", position=1)
    first = _media(tmp_path / "first.jpg", "photo", position=0)
    post = Post(id=1, caption="Подпись", media_files=(second, first))

    result = asyncio.run(
        _publisher(client, session_path).publish(
            post,
            PublishTarget("self", "feed", "Лента"),
        )
    )

    album_call = next(call for call in client.calls if call[0] == "album_upload")
    assert album_call[1] == [tmp_path / "first.jpg", tmp_path / "second.mp4"]
    assert result.success
    assert result.external_id == "instagram-media-id"
    assert client.request_timeout == 45
    assert ("set_proxy", "http://proxy:8080") in client.calls
    assert ("totp_generate_code", "totp-secret") in client.calls
    assert ("login", "user", "password", "123456") in client.calls
    assert any(call[0] == "load_settings" for call in client.calls)
    assert session_path.exists()


def test_photo_story_uses_fit_resize_mode(tmp_path: Path) -> None:
    client = FakeInstagramClient()
    photo = _media(tmp_path / "story.jpg", "photo")

    result = asyncio.run(
        _publisher(client, tmp_path / "session.json").publish(
            Post(id=1, caption="Story", media_files=(photo,)),
            PublishTarget("self", "story", "История"),
        )
    )

    story_call = next(call for call in client.calls if call[0] == "photo_story")
    assert story_call[3] == {"resize_mode": "fit"}
    assert result.success


def test_connection_error_is_retryable(tmp_path: Path) -> None:
    client = FakeInstagramClient(ClientConnectionError("offline"))
    photo = _media(tmp_path / "photo.jpg", "photo")

    result = asyncio.run(
        _publisher(client, tmp_path / "session.json").publish(
            Post(id=1, caption=None, media_files=(photo,)),
            PublishTarget("self", "feed", "Лента"),
        )
    )

    assert not result.success
    assert result.retryable
    assert "ClientConnectionError" in (result.error or "")


def test_bad_password_is_not_retryable(tmp_path: Path) -> None:
    client = FakeInstagramClient(BadPassword("invalid password"))
    photo = _media(tmp_path / "photo.jpg", "photo")

    result = asyncio.run(
        _publisher(client, tmp_path / "session.json").publish(
            Post(id=1, caption=None, media_files=(photo,)),
            PublishTarget("self", "feed", "Лента"),
        )
    )

    assert not result.success
    assert not result.retryable
    assert "BadPassword" in (result.error or "")


def test_rate_limit_uses_long_retry_delay(tmp_path: Path) -> None:
    client = FakeInstagramClient(PleaseWaitFewMinutes("slow down"))
    photo = _media(tmp_path / "photo.jpg", "photo")

    result = asyncio.run(
        _publisher(client, tmp_path / "session.json").publish(
            Post(id=1, caption=None, media_files=(photo,)),
            PublishTarget("self", "feed", "Лента"),
        )
    )

    assert not result.success
    assert result.retryable
    assert result.retry_after == 15 * 60


def test_story_with_multiple_files_is_rejected_before_login(tmp_path: Path) -> None:
    client = FakeInstagramClient()
    first = _media(tmp_path / "first.jpg", "photo")
    second = _media(tmp_path / "second.jpg", "photo", position=1)

    result = asyncio.run(
        _publisher(client, tmp_path / "session.json").publish(
            Post(id=1, caption=None, media_files=(first, second)),
            PublishTarget("self", "story", "История"),
        )
    )

    assert not result.success
    assert not result.retryable
    assert client.calls == []
