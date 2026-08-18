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
    upload_media,
)
from app.publishers.zernio_client import (
    external_id as _external_id,
)
from app.publishers.zernio_client import (
    request_json as _request_json,
)
from app.publishers.zernio_client import (
    safe_error as _safe_error,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZERNIO_MAX_UPLOAD_SIZE = 5 * 1024**3
TIKTOK_VIDEO_MAX_SIZE = 4 * 1024**3
TIKTOK_PHOTO_MAX_SIZE = 20 * 1024**2
TIKTOK_VIDEO_CAPTION_LIMIT = 2200
TIKTOK_PHOTO_DESCRIPTION_LIMIT = 4000
SUPPORTED_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}


class TikTokPublisher:
    platform = "tiktok"

    def __init__(
        self,
        api_key: str,
        account_id: str,
        api_base_url: str = "https://zernio.com/api",
        request_timeout: int = 120,
        privacy_level: str = "PUBLIC_TO_EVERYONE",
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
    ) -> None:
        self.api_key = api_key
        self.account_id = account_id
        self.api_base_url = api_base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.privacy_level = privacy_level
        self.session_factory = session_factory

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        try:
            media_files = _validate_post(post, target)
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with self.session_factory(timeout=timeout) as session:
                media_items = []
                for media in media_files:
                    media_items.append(await self._upload_media(session, media))
                response = await self._create_post(session, post, media_items)
            external_id = _external_id(response)
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
                error=_safe_error(error),
            )
        except (OSError, PublisherError, ValueError) as error:
            return PublishResult(
                success=False,
                retryable=False,
                error=_safe_error(error),
            )
        except Exception as error:
            logger.exception("Unexpected TikTok/Zernio publisher error")
            return PublishResult(
                success=False,
                retryable=True,
                error=_safe_error(error),
            )

        return PublishResult(success=True, external_id=external_id)

    async def _upload_media(
        self,
        session: aiohttp.ClientSession,
        media: MediaFile,
    ) -> dict[str, str]:
        path = _media_path(media)
        return await upload_media(
            session,
            api_key=self.api_key,
            api_base_url=self.api_base_url,
            media=media,
            path=path,
            content_type=_content_type(path, media.media_type),
        )

    async def _create_post(
        self,
        session: aiohttp.ClientSession,
        post: Post,
        media_items: list[dict[str, str]],
    ) -> dict[str, Any]:
        is_photo_post = all(item["type"] == "image" for item in media_items)
        caption = post.caption or ""
        settings: dict[str, Any] = {
            "privacy_level": self.privacy_level,
            "allow_comment": True,
            "content_preview_confirmed": True,
            "express_consent_given": True,
        }
        if is_photo_post:
            settings.update(
                {
                    "media_type": "photo",
                    "description": caption,
                    "photo_cover_index": 0,
                    "auto_add_music": True,
                }
            )
        else:
            settings.update({"allow_duet": True, "allow_stitch": True})

        payload: dict[str, Any] = {
            "mediaItems": media_items,
            "platforms": [
                {
                    "platform": "tiktok",
                    "accountId": self.account_id,
                }
            ],
            "tiktokSettings": settings,
            "publishNow": True,
        }
        if caption:
            payload["content"] = _photo_title(caption) if is_photo_post else caption

        request_id = str(
            uuid5(
                NAMESPACE_URL,
                f"social-autoposting:zernio:tiktok:{self.account_id}:{post.id}",
            )
        )
        return await _request_json(
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
    if target.kind != "feed":
        raise PublisherError(f"Unsupported TikTok target kind: {target.kind}")

    media_files = tuple(sorted(post.media_files, key=lambda media: media.position))
    if not media_files:
        raise PublisherError("TikTok publication requires photo or video")

    media_types = {media.media_type for media in media_files}
    if not media_types <= {"photo", "video"}:
        unsupported = ", ".join(sorted(media_types - {"photo", "video"}))
        raise PublisherError(f"Unsupported TikTok media type: {unsupported}")
    if len(media_types) > 1:
        raise PublisherError("TikTok cannot mix photos and videos")
    if "video" in media_types and len(media_files) != 1:
        raise PublisherError("TikTok requires exactly one video")
    if "photo" in media_types and len(media_files) > 35:
        raise PublisherError("TikTok supports at most 35 photos")

    caption_limit = (
        TIKTOK_VIDEO_CAPTION_LIMIT
        if "video" in media_types
        else TIKTOK_PHOTO_DESCRIPTION_LIMIT
    )
    if len(post.caption or "") > caption_limit:
        raise PublisherError(f"TikTok caption exceeds {caption_limit} characters")

    for media in media_files:
        path = _media_path(media)
        if not path.is_file():
            raise PublisherError(f"TikTok media file does not exist: {path.name}")
        size = path.stat().st_size
        if size > ZERNIO_MAX_UPLOAD_SIZE:
            raise PublisherError("Zernio accepts files up to 5 GB")
        suffix = path.suffix.lower()
        if media.media_type == "photo":
            if suffix not in SUPPORTED_PHOTO_SUFFIXES:
                raise PublisherError("TikTok photos must use JPG, PNG or WebP format")
            if size > TIKTOK_PHOTO_MAX_SIZE:
                raise PublisherError("TikTok photos must not exceed 20 MB")
        elif suffix not in SUPPORTED_VIDEO_SUFFIXES:
            raise PublisherError("TikTok videos must use MP4, MOV or WebM format")
        elif size > TIKTOK_VIDEO_MAX_SIZE:
            raise PublisherError("TikTok videos must not exceed 4 GB")
    return media_files


def _media_path(media: MediaFile) -> Path:
    path = Path(media.file_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _content_type(path: Path, media_type: str) -> str:
    known_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
    }
    guessed = known_types.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
    if guessed is None:
        raise PublisherError(f"Cannot detect TikTok media type: {path.name}")
    expected_prefix = "image/" if media_type == "photo" else "video/"
    if not guessed.startswith(expected_prefix):
        raise PublisherError(f"Invalid TikTok media type for {path.name}")
    return guessed


def _photo_title(caption: str) -> str:
    return caption[:90]
