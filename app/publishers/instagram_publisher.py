from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from instagrapi import Client
from instagrapi.exceptions import (
    ClientConnectionError,
    ClientError,
    ClientIncompleteReadError,
    ClientJSONDecodeError,
    ClientLoginRequired,
    ClientRequestTimeout,
    ClientThrottledError,
    GenericRequestError,
    LoginRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
)
from loguru import logger

from app.publishers.base import (
    MediaFile,
    Post,
    PublisherError,
    PublishResult,
    PublishTarget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTAGRAM_CAPTION_LIMIT = 2200
SUPPORTED_PHOTO_SUFFIXES = {".jpg", ".jpeg"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4"}
INSTAGRAM_RATE_LIMIT_RETRY_SECONDS = 15 * 60
RATE_LIMIT_EXCEPTIONS = (
    ClientThrottledError,
    PleaseWaitFewMinutes,
    RateLimitError,
)
RETRYABLE_EXCEPTIONS = (
    ClientConnectionError,
    ClientIncompleteReadError,
    ClientJSONDecodeError,
    ClientLoginRequired,
    ClientRequestTimeout,
    GenericRequestError,
    LoginRequired,
)


class InstagramPublisher:
    platform = "instagram"

    def __init__(
        self,
        username: str,
        password: str,
        totp_secret: str | None = None,
        session_path: str | Path = "data/instagram_session.json",
        proxy: str | None = None,
        request_timeout: int = 30,
        client_factory: Callable[[], Client] = Client,
    ) -> None:
        self.username = username
        self.password = password
        self.totp_secret = totp_secret
        self.session_path = _absolute_path(Path(session_path))
        self.proxy = proxy
        self.request_timeout = request_timeout
        self.client_factory = client_factory

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        try:
            _validate_post(post, target)
            external_id = await asyncio.to_thread(self._publish_sync, post, target)
        except RATE_LIMIT_EXCEPTIONS as error:
            return PublishResult(
                success=False,
                retryable=True,
                error=_safe_error(error),
                retry_after=INSTAGRAM_RATE_LIMIT_RETRY_SECONDS,
            )
        except RETRYABLE_EXCEPTIONS as error:
            return PublishResult(
                success=False,
                retryable=True,
                error=_safe_error(error),
            )
        except (ClientError, PublisherError, OSError, ValueError) as error:
            return PublishResult(
                success=False,
                retryable=False,
                error=_safe_error(error),
            )
        except Exception as error:
            logger.exception("Unexpected Instagram publisher error")
            return PublishResult(
                success=False,
                retryable=True,
                error=_safe_error(error),
            )

        return PublishResult(success=True, external_id=external_id)

    def _publish_sync(self, post: Post, target: PublishTarget) -> str:
        client = self._authenticated_client()
        media_files = _ordered_media(post)
        paths = [_media_path(media) for media in media_files]
        caption = post.caption or ""

        if target.kind == "feed":
            uploaded = self._publish_feed(client, media_files, paths, caption)
        else:
            uploaded = self._publish_story(client, media_files[0], paths[0], caption)
        return _external_id(uploaded)

    def _authenticated_client(self) -> Client:
        client = self.client_factory()
        client.request_timeout = self.request_timeout
        if self.proxy:
            client.set_proxy(self.proxy)

        self._load_session(client)
        verification_code = (
            client.totp_generate_code(self.totp_secret) if self.totp_secret else ""
        )
        if not client.login(
            self.username,
            self.password,
            verification_code=verification_code,
        ):
            raise PublisherError("Instagram rejected the login attempt")
        self._save_session(client)
        return client

    def _load_session(self, client: Client) -> None:
        if not self.session_path.exists():
            return
        try:
            settings = client.load_settings(self.session_path)
            if settings:
                client.set_settings(settings)
        except (OSError, ValueError) as error:
            logger.warning(
                "Cannot load Instagram session from {}: {}",
                self.session_path,
                type(error).__name__,
            )
            client.set_settings({})

    def _save_session(self, client: Client) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.session_path.with_suffix(
            f"{self.session_path.suffix}.tmp"
        )
        client.dump_settings(temporary_path)
        os.replace(temporary_path, self.session_path)
        self.session_path.chmod(0o600)

    @staticmethod
    def _publish_feed(
        client: Client,
        media_files: tuple[MediaFile, ...],
        paths: list[Path],
        caption: str,
    ) -> Any:
        if len(paths) > 1:
            return client.album_upload(paths, caption)
        if media_files[0].media_type == "photo":
            return client.photo_upload(paths[0], caption)
        return client.video_upload(paths[0], caption)

    @staticmethod
    def _publish_story(
        client: Client,
        media: MediaFile,
        path: Path,
        caption: str,
    ) -> Any:
        if media.media_type == "photo":
            return client.photo_upload_to_story(path, caption, resize_mode="fit")
        return client.video_upload_to_story(path, caption, resize_mode="fit")


def _validate_post(post: Post, target: PublishTarget) -> None:
    media_files = _ordered_media(post)
    if target.kind not in {"feed", "story"}:
        raise PublisherError(f"Unsupported Instagram target kind: {target.kind}")
    if not media_files:
        raise PublisherError("Instagram publication requires photo or video")
    if target.kind == "story" and len(media_files) != 1:
        raise PublisherError("Instagram story requires exactly one media file")
    if len(post.caption or "") > INSTAGRAM_CAPTION_LIMIT:
        raise PublisherError(
            f"Instagram caption exceeds {INSTAGRAM_CAPTION_LIMIT} characters"
        )

    for media in media_files:
        path = _media_path(media)
        if not path.is_file():
            raise PublisherError(f"Instagram media file does not exist: {path.name}")
        suffix = path.suffix.lower()
        if media.media_type == "photo" and suffix not in SUPPORTED_PHOTO_SUFFIXES:
            raise PublisherError("Instagram photos must use JPG format")
        if media.media_type == "video" and suffix not in SUPPORTED_VIDEO_SUFFIXES:
            raise PublisherError("Instagram videos must use MP4 format")
        if media.media_type not in {"photo", "video"}:
            raise PublisherError(
                f"Unsupported Instagram media type: {media.media_type}"
            )


def _ordered_media(post: Post) -> tuple[MediaFile, ...]:
    return tuple(sorted(post.media_files, key=lambda media: media.position))


def _media_path(media: MediaFile) -> Path:
    return _absolute_path(Path(media.file_path))


def _absolute_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _external_id(uploaded: Any) -> str:
    external_id = getattr(uploaded, "id", None) or getattr(uploaded, "pk", None)
    if external_id is None and isinstance(uploaded, dict):
        external_id = uploaded.get("id") or uploaded.get("pk")
    if external_id is None:
        raise PublisherError("Instagram upload returned no media identifier")
    return str(external_id)


def _safe_error(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__
