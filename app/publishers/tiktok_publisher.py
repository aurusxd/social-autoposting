from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from app.publishers.base import (
    MediaFile,
    Post,
    PublisherError,
    PublishResult,
    PublishTarget,
)
from app.publishers.public_media import absolute_path, media_path, public_url
from app.publishers.tiktok_client import (
    TikTokAPIError,
    TikTokTokenProvider,
    TokenStore,
    request_data,
    required_data_string,
    safe_error,
    upload_chunk,
)

TIKTOK_VIDEO_MAX_SIZE = 4 * 1024**3
TIKTOK_PHOTO_MAX_SIZE = 20 * 1024**2
TIKTOK_VIDEO_CAPTION_LIMIT = 2200
TIKTOK_PHOTO_TITLE_LIMIT = 90
TIKTOK_PHOTO_DESCRIPTION_LIMIT = 4000
TIKTOK_PHOTO_LIMIT = 35
SUPPORTED_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
# Chunk constraints imposed by the Content Posting API.
MIN_CHUNK_SIZE = 5 * 1024**2
MAX_CHUNK_SIZE = 64 * 1024**2
MAX_CHUNK_COUNT = 1000


class TikTokPublisher:
    """Publishes through the official TikTok Content Posting API (direct post)."""

    platform = "tiktok"

    def __init__(
        self,
        client_key: str,
        client_secret: str,
        refresh_token: str,
        media_base_url: str,
        media_root: str | Path = "media",
        api_base_url: str = "https://open.tiktokapis.com",
        request_timeout: int = 300,
        privacy_level: str = "PUBLIC_TO_EVERYONE",
        disable_comment: bool = False,
        disable_duet: bool = False,
        disable_stitch: bool = False,
        auto_add_music: bool = True,
        chunk_size: int = 10 * 1024**2,
        status_poll_interval: int = 5,
        status_poll_attempts: int = 60,
        token_store: TokenStore | None = None,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.media_base_url = media_base_url.rstrip("/")
        self.media_root = absolute_path(Path(media_root)).resolve()
        self.request_timeout = request_timeout
        self.privacy_level = privacy_level
        self.disable_comment = disable_comment
        self.disable_duet = disable_duet
        self.disable_stitch = disable_stitch
        self.auto_add_music = auto_add_music
        self.chunk_size = chunk_size
        self.status_poll_interval = status_poll_interval
        self.status_poll_attempts = status_poll_attempts
        self.session_factory = session_factory
        self.sleep = sleep
        self.tokens = TikTokTokenProvider(
            client_key=client_key,
            client_secret=client_secret,
            refresh_token=refresh_token,
            api_base_url=self.api_base_url,
            store=token_store,
        )

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        try:
            media_files = _validate_post(post, target)
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with self.session_factory(timeout=timeout) as session:
                access_token = await self.tokens.access_token(session)
                creator = await self._query_creator_info(session, access_token)
                is_photo_post = media_files[0].media_type == "photo"
                if is_photo_post:
                    publish_id = await self._post_photos(
                        session,
                        access_token,
                        creator,
                        post,
                        media_files,
                    )
                else:
                    publish_id = await self._post_video(
                        session,
                        access_token,
                        creator,
                        post,
                        media_files[0],
                    )
                await self._wait_for_publish(session, access_token, publish_id)
        except TikTokAPIError as error:
            return PublishResult(
                success=False,
                retryable=error.retryable,
                error=str(error),
                retry_after=error.retry_after,
            )
        except (aiohttp.ClientError, TimeoutError) as error:
            return PublishResult(success=False, retryable=True, error=safe_error(error))
        except (OSError, PublisherError, ValueError) as error:
            return PublishResult(
                success=False,
                retryable=False,
                error=safe_error(error),
            )
        except Exception as error:
            logger.exception("Unexpected TikTok publisher error")
            return PublishResult(success=False, retryable=True, error=safe_error(error))

        return PublishResult(success=True, external_id=publish_id)

    async def _query_creator_info(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
    ) -> dict[str, Any]:
        """TikTok requires the creator query before every direct post."""
        creator = await request_data(
            session,
            f"{self.api_base_url}/v2/post/publish/creator_info/query/",
            access_token=access_token,
        )
        options = creator.get("privacy_level_options")
        if isinstance(options, list) and options and self.privacy_level not in options:
            raise PublisherError(
                f"TikTok account does not allow privacy level {self.privacy_level}; "
                f"available: {', '.join(str(option) for option in options)}"
            )
        return creator

    async def _post_video(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        creator: dict[str, Any],
        post: Post,
        media: MediaFile,
    ) -> str:
        path = media_path(media)
        size = path.stat().st_size
        chunk_size, total_chunks = _chunk_plan(size, self.chunk_size)

        post_info: dict[str, Any] = {
            "title": (post.caption or "")[:TIKTOK_VIDEO_CAPTION_LIMIT],
            "privacy_level": self.privacy_level,
            "disable_comment": self._disabled(creator, "comment", self.disable_comment),
            "disable_duet": self._disabled(creator, "duet", self.disable_duet),
            "disable_stitch": self._disabled(creator, "stitch", self.disable_stitch),
        }
        data = await request_data(
            session,
            f"{self.api_base_url}/v2/post/publish/video/init/",
            access_token=access_token,
            json_body={
                "post_info": post_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunks,
                },
            },
        )
        publish_id = required_data_string(data, "publish_id")
        upload_url = required_data_string(data, "upload_url")
        await self._upload_video(
            session,
            upload_url,
            path=path,
            size=size,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            content_type=_content_type(path, media.media_type),
        )
        return publish_id

    async def _upload_video(
        self,
        session: aiohttp.ClientSession,
        upload_url: str,
        *,
        path: Path,
        size: int,
        chunk_size: int,
        total_chunks: int,
        content_type: str,
    ) -> None:
        with path.open("rb") as source:
            for index in range(total_chunks):
                first_byte = index * chunk_size
                # TikTok expects the trailing bytes to ride along with the last chunk.
                length = size - first_byte if index + 1 == total_chunks else chunk_size
                source.seek(first_byte)
                chunk = source.read(length)
                if not chunk:
                    raise PublisherError("TikTok video file ended before upload done")
                await upload_chunk(
                    session,
                    upload_url,
                    chunk=chunk,
                    first_byte=first_byte,
                    total_size=size,
                    content_type=content_type,
                )

    async def _post_photos(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        creator: dict[str, Any],
        post: Post,
        media_files: tuple[MediaFile, ...],
    ) -> str:
        caption = post.caption or ""
        photo_images = [
            public_url(
                media_path(media),
                media_base_url=self.media_base_url,
                media_root=self.media_root,
                platform="TikTok",
            )
            for media in media_files
        ]
        data = await request_data(
            session,
            f"{self.api_base_url}/v2/post/publish/content/init/",
            access_token=access_token,
            json_body={
                "post_info": {
                    "title": caption[:TIKTOK_PHOTO_TITLE_LIMIT],
                    "description": caption[:TIKTOK_PHOTO_DESCRIPTION_LIMIT],
                    "privacy_level": self.privacy_level,
                    "disable_comment": self._disabled(
                        creator,
                        "comment",
                        self.disable_comment,
                    ),
                    "auto_add_music": self.auto_add_music,
                },
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "photo_images": photo_images,
                    "photo_cover_index": 0,
                },
                "post_mode": "DIRECT_POST",
                "media_type": "PHOTO",
            },
        )
        return required_data_string(data, "publish_id")

    async def _wait_for_publish(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        publish_id: str,
    ) -> None:
        for attempt in range(self.status_poll_attempts):
            data = await request_data(
                session,
                f"{self.api_base_url}/v2/post/publish/status/fetch/",
                access_token=access_token,
                json_body={"publish_id": publish_id},
            )
            status = data.get("status")
            if status == "PUBLISH_COMPLETE":
                return
            if status == "FAILED":
                raise PublisherError(
                    f"TikTok rejected the post: "
                    f"{data.get('fail_reason') or 'no reason given'}"
                )
            if attempt + 1 < self.status_poll_attempts:
                await self.sleep(self.status_poll_interval)

        # The post is already accepted; retrying here would duplicate it.
        logger.warning(
            "TikTok publication {} is still processing after {} seconds",
            publish_id,
            self.status_poll_interval * self.status_poll_attempts,
        )

    def _disabled(
        self,
        creator: dict[str, Any],
        feature: str,
        configured: bool,
    ) -> bool:
        """Honour the interaction toggles the account itself has switched off."""
        return bool(configured or creator.get(f"{feature}_disabled") is True)


def _chunk_plan(size: int, preferred_chunk_size: int) -> tuple[int, int]:
    if size <= 0:
        raise PublisherError("TikTok video file is empty")
    if size <= MAX_CHUNK_SIZE:
        return size, 1

    chunk_size = min(max(preferred_chunk_size, MIN_CHUNK_SIZE), MAX_CHUNK_SIZE)
    total_chunks = size // chunk_size
    if total_chunks > MAX_CHUNK_COUNT:
        total_chunks = MAX_CHUNK_COUNT
        chunk_size = size // total_chunks
        if chunk_size > MAX_CHUNK_SIZE:
            raise PublisherError("TikTok video is too large to upload in 1000 chunks")
    return chunk_size, total_chunks


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
    if "photo" in media_types and len(media_files) > TIKTOK_PHOTO_LIMIT:
        raise PublisherError(f"TikTok supports at most {TIKTOK_PHOTO_LIMIT} photos")

    caption_limit = (
        TIKTOK_VIDEO_CAPTION_LIMIT
        if "video" in media_types
        else TIKTOK_PHOTO_DESCRIPTION_LIMIT
    )
    if len(post.caption or "") > caption_limit:
        raise PublisherError(f"TikTok caption exceeds {caption_limit} characters")

    for media in media_files:
        path = media_path(media)
        if not path.is_file():
            raise PublisherError(f"TikTok media file does not exist: {path.name}")
        size = path.stat().st_size
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
