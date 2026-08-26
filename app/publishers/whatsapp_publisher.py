from __future__ import annotations

import mimetypes
from collections.abc import Callable
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
from app.publishers.whapi_client import (
    WhapiHTTPError,
    message_id,
    request_json,
    send_media,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WHATSAPP_TEXT_LIMIT = 4096
WHATSAPP_MEDIA_CAPTION_LIMIT = 1024
SUPPORTED_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".3gp"}
SUPPORTED_TARGET_KINDS = {"group", "channel"}


class WhatsAppPublisher:
    """Publishes to WhatsApp groups and channels through the Whapi.Cloud API."""

    platform = "whatsapp"

    def __init__(
        self,
        api_token: str,
        api_url: str = "https://gate.whapi.cloud",
        request_timeout: int = 120,
        media_max_bytes: int = 100 * 1024**2,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
    ) -> None:
        self.api_token = api_token
        self.api_url = api_url.rstrip("/")
        self.request_timeout = request_timeout
        self.media_max_bytes = media_max_bytes
        self.session_factory = session_factory

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        sent_ids: list[str] = []
        try:
            media_files = _validate_post(post, target, self.media_max_bytes)
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with self.session_factory(timeout=timeout) as session:
                caption = post.caption or ""
                if not media_files:
                    response = await self._send_text(session, target.key, caption)
                    sent_ids.append(message_id(response))
                else:
                    media_caption = caption
                    if len(caption) > WHATSAPP_MEDIA_CAPTION_LIMIT:
                        # Too long to ride along with the media; send it on its own.
                        response = await self._send_text(session, target.key, caption)
                        sent_ids.append(message_id(response))
                        media_caption = ""

                    for index, media in enumerate(media_files):
                        response = await self._send_media(
                            session,
                            target.key,
                            media,
                            media_caption if index == 0 else "",
                        )
                        sent_ids.append(message_id(response))
        except WhapiHTTPError as error:
            return _failure_result(
                error,
                retryable=error.retryable,
                retry_after=error.retry_after,
                sent_ids=sent_ids,
            )
        except (aiohttp.ClientError, TimeoutError) as error:
            return _failure_result(error, retryable=True, sent_ids=sent_ids)
        except (OSError, PublisherError, ValueError) as error:
            return _failure_result(error, retryable=False, sent_ids=sent_ids)
        except Exception as error:
            logger.exception("Unexpected WhatsApp/Whapi publisher error")
            return _failure_result(error, retryable=True, sent_ids=sent_ids)

        return PublishResult(success=True, external_id=",".join(sent_ids))

    async def _send_text(
        self,
        session: aiohttp.ClientSession,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        return await request_json(
            session,
            "POST",
            f"{self.api_url}/messages/text",
            token=self.api_token,
            json={"to": chat_id, "body": text},
        )

    async def _send_media(
        self,
        session: aiohttp.ClientSession,
        chat_id: str,
        media: MediaFile,
        caption: str,
    ) -> dict[str, Any]:
        path = _media_path(media)
        endpoint = "image" if media.media_type == "photo" else "video"
        return await send_media(
            session,
            f"{self.api_url}/messages/{endpoint}",
            token=self.api_token,
            path=path,
            to=chat_id,
            caption=caption,
            content_type=_content_type(path, media.media_type),
        )


def _validate_post(
    post: Post,
    target: PublishTarget,
    media_max_bytes: int,
) -> tuple[MediaFile, ...]:
    if target.kind not in SUPPORTED_TARGET_KINDS:
        raise PublisherError(f"Unsupported WhatsApp target kind: {target.kind}")
    if not target.key.strip():
        raise PublisherError("WhatsApp target has an empty chat id")

    caption = post.caption or ""
    if len(caption) > WHATSAPP_TEXT_LIMIT:
        raise PublisherError(f"WhatsApp text exceeds {WHATSAPP_TEXT_LIMIT} characters")

    media_files = tuple(sorted(post.media_files, key=lambda media: media.position))
    if not caption and not media_files:
        raise PublisherError("WhatsApp publication requires text, photo or video")

    for media in media_files:
        path = _media_path(media)
        if not path.is_file():
            raise PublisherError(f"WhatsApp media file does not exist: {path.name}")
        if path.stat().st_size > media_max_bytes:
            limit_mb = media_max_bytes // 1024**2
            raise PublisherError(f"WhatsApp media must not exceed {limit_mb} MB")
        suffix = path.suffix.lower()
        if media.media_type == "photo":
            if suffix not in SUPPORTED_PHOTO_SUFFIXES:
                raise PublisherError(
                    "WhatsApp photos must use JPG, PNG, GIF or WebP format"
                )
        elif media.media_type == "video":
            if suffix not in SUPPORTED_VIDEO_SUFFIXES:
                raise PublisherError("WhatsApp videos must use MP4 or 3GP format")
        else:
            raise PublisherError(f"Unsupported WhatsApp media type: {media.media_type}")
    return media_files


def _media_path(media: MediaFile) -> Path:
    path = Path(media.file_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _content_type(path: Path, media_type: str) -> str:
    known_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".3gp": "video/3gpp",
    }
    detected = (
        known_types.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
    )
    expected_prefix = "image/" if media_type == "photo" else "video/"
    if detected is None or not detected.startswith(expected_prefix):
        raise PublisherError(f"Invalid WhatsApp media type for {path.name}")
    return detected


def _failure_result(
    error: Exception,
    *,
    retryable: bool,
    sent_ids: list[str],
    retry_after: int | None = None,
) -> PublishResult:
    message = str(error).strip()
    error_text = (
        f"{type(error).__name__}: {message}" if message else type(error).__name__
    )
    if sent_ids:
        error_text = (
            f"Partial WhatsApp publication ({len(sent_ids)} messages sent); "
            f"automatic retry disabled: {error_text}"
        )
    return PublishResult(
        success=False,
        retryable=retryable and not sent_ids,
        error=error_text,
        retry_after=retry_after if not sent_ids else None,
    )
