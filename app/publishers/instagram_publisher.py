from __future__ import annotations

import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import aiohttp
from loguru import logger

from app.publishers.base import (
    MediaFile,
    Post,
    PublisherError,
    PublishResult,
    PublishTarget,
)
from app.publishers.zernio_client import (
    RETRYABLE_HTTP_STATUSES,
    ZernioHTTPError,
    external_id,
    request_json,
    safe_error,
    upload_media,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTAGRAM_CAPTION_LIMIT = 2200
INSTAGRAM_CAROUSEL_LIMIT = 10
INSTAGRAM_PHOTO_MAX_SIZE = 8 * 1024**2
INSTAGRAM_VIDEO_MAX_SIZE = 300 * 1024**2
INSTAGRAM_STORY_VIDEO_MAX_SIZE = 100 * 1024**2
SUPPORTED_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov"}


class InstagramPublisher:
    platform = "instagram"

    def __init__(
        self,
        api_key: str,
        account_id: str,
        api_base_url: str = "https://zernio.com/api",
        request_timeout: int = 120,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
    ) -> None:
        self.api_key = api_key
        self.account_id = account_id
        self.api_base_url = api_base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.session_factory = session_factory

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        try:
            media_files = _validate_post(post, target)
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with self.session_factory(timeout=timeout) as session:
                media_items = []
                for media in media_files:
                    path = _media_path(media)
                    media_items.append(
                        await upload_media(
                            session,
                            api_key=self.api_key,
                            api_base_url=self.api_base_url,
                            media=media,
                            path=path,
                            content_type=_content_type(path, media.media_type),
                        )
                    )
                response = await self._create_post(
                    session,
                    post,
                    target,
                    media_items,
                )
            post_id = external_id(response)
        except ZernioHTTPError as error:
            return PublishResult(
                success=False,
                retryable=error.status in RETRYABLE_HTTP_STATUSES,
                error=str(error),
                retry_after=error.retry_after,
            )
        except (aiohttp.ClientError, TimeoutError) as error:
            return PublishResult(
                success=False,
                retryable=True,
                error=safe_error(error),
            )
        except (OSError, PublisherError, ValueError) as error:
            return PublishResult(
                success=False,
                retryable=False,
                error=safe_error(error),
            )
        except Exception as error:
            logger.exception("Unexpected Instagram/Zernio publisher error")
            return PublishResult(
                success=False,
                retryable=True,
                error=safe_error(error),
            )

        return PublishResult(success=True, external_id=post_id)

    async def _create_post(
        self,
        session: aiohttp.ClientSession,
        post: Post,
        target: PublishTarget,
        media_items: list[dict[str, str]],
    ) -> dict[str, Any]:
        platform: dict[str, Any] = {
            "platform": "instagram",
            "accountId": self.account_id,
        }
        if target.kind == "story":
            platform["platformSpecificData"] = {"contentType": "story"}
        elif len(media_items) == 1 and media_items[0]["type"] == "video":
            platform["platformSpecificData"] = {"shareToFeed": True}

        payload: dict[str, Any] = {
            "mediaItems": media_items,
            "platforms": [platform],
            "publishNow": True,
        }
        if target.kind == "feed" and post.caption:
            payload["content"] = post.caption

        request_id = str(
            uuid5(
                NAMESPACE_URL,
                "social-autoposting:zernio:instagram:"
                f"{self.account_id}:{post.id}:{target.kind}",
            )
        )
        return await request_json(
            session,
            "POST",
            f"{self.api_base_url}/v1/posts",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "x-request-id": request_id,
            },
        )


def _validate_post(post: Post, target: PublishTarget) -> tuple[MediaFile, ...]:
    if target.kind not in {"feed", "story"}:
        raise PublisherError(f"Unsupported Instagram target kind: {target.kind}")

    media_files = tuple(sorted(post.media_files, key=lambda media: media.position))
    if not media_files:
        raise PublisherError("Instagram publication requires photo or video")
    if target.kind == "story" and len(media_files) != 1:
        raise PublisherError("Instagram story requires exactly one media file")
    if target.kind == "feed" and len(media_files) > INSTAGRAM_CAROUSEL_LIMIT:
        raise PublisherError("Instagram supports at most 10 carousel items")
    if len(post.caption or "") > INSTAGRAM_CAPTION_LIMIT:
        raise PublisherError(
            f"Instagram caption exceeds {INSTAGRAM_CAPTION_LIMIT} characters"
        )

    for media in media_files:
        path = _media_path(media)
        if not path.is_file():
            raise PublisherError(f"Instagram media file does not exist: {path.name}")
        size = path.stat().st_size
        suffix = path.suffix.lower()
        if media.media_type == "photo":
            if suffix not in SUPPORTED_PHOTO_SUFFIXES:
                raise PublisherError("Instagram photos must use JPG or PNG format")
            if size > INSTAGRAM_PHOTO_MAX_SIZE:
                raise PublisherError("Instagram photos must not exceed 8 MB")
        elif media.media_type == "video":
            if suffix not in SUPPORTED_VIDEO_SUFFIXES:
                raise PublisherError("Instagram videos must use MP4 or MOV format")
            size_limit = (
                INSTAGRAM_STORY_VIDEO_MAX_SIZE
                if target.kind == "story"
                else INSTAGRAM_VIDEO_MAX_SIZE
            )
            if size > size_limit:
                limit = 100 if target.kind == "story" else 300
                raise PublisherError(
                    f"Instagram {target.kind} videos must not exceed {limit} MB"
                )
        else:
            raise PublisherError(
                f"Unsupported Instagram media type: {media.media_type}"
            )
    return media_files


def _media_path(media: MediaFile) -> Path:
    path = Path(media.file_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _content_type(path: Path, media_type: str) -> str:
    known_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }
    guessed = known_types.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
    if guessed is None:
        raise PublisherError(f"Cannot detect Instagram media type: {path.name}")
    expected_prefix = "image/" if media_type == "photo" else "video/"
    if not guessed.startswith(expected_prefix):
        raise PublisherError(f"Invalid Instagram media type for {path.name}")
    return guessed
