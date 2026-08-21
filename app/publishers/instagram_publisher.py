from __future__ import annotations

import asyncio
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
from app.publishers.graph_client import (
    GraphAPIError,
    request_json,
    required_response_string,
    safe_error,
)
from app.publishers.public_media import absolute_path, media_path, public_url

INSTAGRAM_CAPTION_LIMIT = 2200
INSTAGRAM_CAROUSEL_LIMIT = 10
INSTAGRAM_PHOTO_MAX_SIZE = 8 * 1024**2
INSTAGRAM_VIDEO_MAX_SIZE = 1024**3
INSTAGRAM_STORY_VIDEO_MAX_SIZE = 100 * 1024**2
SUPPORTED_PHOTO_SUFFIXES = {".jpg", ".jpeg"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov"}


class InstagramPublisher:
    """Publishes through the official Instagram Graph content publishing API."""

    platform = "instagram"

    def __init__(
        self,
        access_token: str,
        ig_user_id: str,
        media_base_url: str,
        media_root: str | Path = "media",
        api_base_url: str = "https://graph.facebook.com",
        api_version: str = "v25.0",
        request_timeout: int = 120,
        status_poll_interval: int = 5,
        status_poll_attempts: int = 60,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.media_base_url = media_base_url.rstrip("/")
        self.media_root = absolute_path(Path(media_root)).resolve()
        self.api_base_url = api_base_url.rstrip("/")
        self.api_version = api_version.strip("/")
        self.request_timeout = request_timeout
        self.status_poll_interval = status_poll_interval
        self.status_poll_attempts = status_poll_attempts
        self.session_factory = session_factory
        self.sleep = sleep

    @property
    def account_url(self) -> str:
        return f"{self.api_base_url}/{self.api_version}/{self.ig_user_id}"

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        try:
            media_files = _validate_post(post, target)
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with self.session_factory(timeout=timeout) as session:
                container_id = await self._build_container(
                    session,
                    post,
                    target,
                    media_files,
                )
                media_id = await self._publish_container(session, container_id)
        except GraphAPIError as error:
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
            logger.exception("Unexpected Instagram publisher error")
            return PublishResult(success=False, retryable=True, error=safe_error(error))

        return PublishResult(success=True, external_id=media_id)

    async def _build_container(
        self,
        session: aiohttp.ClientSession,
        post: Post,
        target: PublishTarget,
        media_files: tuple[MediaFile, ...],
    ) -> str:
        caption = (post.caption or "").strip()

        if target.kind == "story":
            return await self._create_container(
                session,
                media_files[0],
                media_type="STORIES",
            )

        if len(media_files) == 1:
            media = media_files[0]
            if media.media_type == "video":
                return await self._create_container(
                    session,
                    media,
                    media_type="REELS",
                    caption=caption,
                    extra={"share_to_feed": "true"},
                )
            return await self._create_container(
                session,
                media,
                media_type="IMAGE",
                caption=caption,
            )

        children = []
        for media in media_files:
            children.append(
                await self._create_container(
                    session,
                    media,
                    # REELS cannot be a carousel item; plain VIDEO can.
                    media_type="IMAGE" if media.media_type == "photo" else "VIDEO",
                    extra={"is_carousel_item": "true"},
                )
            )

        return await self._create_carousel(session, children, caption)

    async def _create_container(
        self,
        session: aiohttp.ClientSession,
        media: MediaFile,
        *,
        media_type: str,
        caption: str = "",
        extra: dict[str, str] | None = None,
    ) -> str:
        path = media_path(media)
        url = public_url(
            path,
            media_base_url=self.media_base_url,
            media_root=self.media_root,
            platform="Instagram",
        )
        payload: dict[str, str] = {"media_type": media_type}
        payload["image_url" if media.media_type == "photo" else "video_url"] = url
        if caption:
            payload["caption"] = caption
        if extra:
            payload.update(extra)

        response = await request_json(
            session,
            "POST",
            f"{self.account_url}/media",
            access_token=self.access_token,
            data=payload,
        )
        container_id = required_response_string(response, "id")
        await self._wait_for_container(session, container_id)
        return container_id

    async def _create_carousel(
        self,
        session: aiohttp.ClientSession,
        children: list[str],
        caption: str,
    ) -> str:
        payload: dict[str, str] = {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
        }
        if caption:
            payload["caption"] = caption

        response = await request_json(
            session,
            "POST",
            f"{self.account_url}/media",
            access_token=self.access_token,
            data=payload,
        )
        container_id = required_response_string(response, "id")
        await self._wait_for_container(session, container_id)
        return container_id

    async def _wait_for_container(
        self,
        session: aiohttp.ClientSession,
        container_id: str,
    ) -> None:
        """Poll the container until Instagram finished transcoding the media."""
        for attempt in range(self.status_poll_attempts):
            response = await request_json(
                session,
                "GET",
                f"{self.api_base_url}/{self.api_version}/{container_id}",
                access_token=self.access_token,
                params={"fields": "status_code,status"},
            )
            status_code = response.get("status_code")
            if status_code == "FINISHED":
                return
            if status_code in {"ERROR", "EXPIRED"}:
                raise PublisherError(
                    f"Instagram container {container_id} is {status_code}: "
                    f"{response.get('status') or 'no details'}"
                )
            if attempt + 1 < self.status_poll_attempts:
                await self.sleep(self.status_poll_interval)

        raise PublisherError(
            f"Instagram container {container_id} was not ready in "
            f"{self.status_poll_interval * self.status_poll_attempts} seconds"
        )

    async def _publish_container(
        self,
        session: aiohttp.ClientSession,
        container_id: str,
    ) -> str:
        response: dict[str, Any] = await request_json(
            session,
            "POST",
            f"{self.account_url}/media_publish",
            access_token=self.access_token,
            data={"creation_id": container_id},
        )
        return required_response_string(response, "id")


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
        path = media_path(media)
        if not path.is_file():
            raise PublisherError(f"Instagram media file does not exist: {path.name}")
        size = path.stat().st_size
        suffix = path.suffix.lower()
        if media.media_type == "photo":
            if suffix not in SUPPORTED_PHOTO_SUFFIXES:
                raise PublisherError("Instagram photos must use JPEG format")
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
                limit = 100 if target.kind == "story" else 1024
                raise PublisherError(
                    f"Instagram {target.kind} videos must not exceed {limit} MB"
                )
        else:
            raise PublisherError(
                f"Unsupported Instagram media type: {media.media_type}"
            )
    return media_files
