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
from app.publishers.graph_client import GraphAPIError, request_json
from app.publishers.public_media import media_path

WHATSAPP_TEXT_LIMIT = 4096
WHATSAPP_MEDIA_CAPTION_LIMIT = 1024
SUPPORTED_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".3gp"}
# Cloud API caps images at 5 MB and videos at 16 MB regardless of our own limit.
CLOUD_PHOTO_MAX_SIZE = 5 * 1024**2
CLOUD_VIDEO_MAX_SIZE = 16 * 1024**2


class WhatsAppCloudPublisher:
    """Publishes through the official WhatsApp Business Cloud API.

    Handles group chats (`recipient_type: group`) and one-to-one chats
    (`recipient_type: individual`). WhatsApp Channels have no official API at
    all, so `channel` targets must stay on the OpenWA engine.
    """

    platform = "whatsapp"

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_base_url: str = "https://graph.facebook.com",
        api_version: str = "v25.0",
        request_timeout: int = 120,
        media_max_bytes: int = CLOUD_VIDEO_MAX_SIZE,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
    ) -> None:
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_base_url = api_base_url.rstrip("/")
        self.api_version = api_version.strip("/")
        self.request_timeout = request_timeout
        self.media_max_bytes = media_max_bytes
        self.session_factory = session_factory

    @property
    def number_url(self) -> str:
        return f"{self.api_base_url}/{self.api_version}/{self.phone_number_id}"

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        sent_ids: list[str] = []
        try:
            media_files = _validate_post(post, target, self.media_max_bytes)
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with self.session_factory(timeout=timeout) as session:
                caption = post.caption or ""
                if not media_files:
                    sent_ids.append(await self._send_text(session, target, caption))
                else:
                    media_caption = caption
                    if len(caption) > WHATSAPP_MEDIA_CAPTION_LIMIT:
                        # A caption longer than the media limit goes out on its own.
                        sent_ids.append(await self._send_text(session, target, caption))
                        media_caption = ""

                    for index, media in enumerate(media_files):
                        media_id = await self._upload_media(session, media)
                        sent_ids.append(
                            await self._send_media(
                                session,
                                target,
                                media,
                                media_id,
                                media_caption if index == 0 else "",
                            )
                        )
        except GraphAPIError as error:
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
            logger.exception("Unexpected WhatsApp Cloud publisher error")
            return _failure_result(error, retryable=True, sent_ids=sent_ids)

        return PublishResult(success=True, external_id=",".join(sent_ids))

    async def _upload_media(
        self,
        session: aiohttp.ClientSession,
        media: MediaFile,
    ) -> str:
        """Upload the file to /media and return the reusable media id."""
        path = media_path(media)
        content_type = _content_type(path, media.media_type)
        form = aiohttp.FormData()
        form.add_field("messaging_product", "whatsapp")
        form.add_field("type", content_type)
        form.add_field(
            "file",
            path.read_bytes(),
            filename=path.name,
            content_type=content_type,
        )
        response = await request_json(
            session,
            "POST",
            f"{self.number_url}/media",
            access_token=self.access_token,
            data=form,
        )
        media_id = response.get("id")
        if not isinstance(media_id, str) or not media_id:
            raise PublisherError("WhatsApp Cloud API returned no media id")
        return media_id

    async def _send_text(
        self,
        session: aiohttp.ClientSession,
        target: PublishTarget,
        text: str,
    ) -> str:
        return await self._send(
            session,
            target,
            {"type": "text", "text": {"preview_url": True, "body": text}},
        )

    async def _send_media(
        self,
        session: aiohttp.ClientSession,
        target: PublishTarget,
        media: MediaFile,
        media_id: str,
        caption: str,
    ) -> str:
        message_type = "image" if media.media_type == "photo" else "video"
        payload: dict[str, Any] = {"id": media_id}
        if caption:
            payload["caption"] = caption
        return await self._send(
            session,
            target,
            {"type": message_type, message_type: payload},
        )

    async def _send(
        self,
        session: aiohttp.ClientSession,
        target: PublishTarget,
        message: dict[str, Any],
    ) -> str:
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": _recipient_type(target),
            "to": target.key,
        }
        body.update(message)
        response = await request_json(
            session,
            "POST",
            f"{self.number_url}/messages",
            access_token=self.access_token,
            json=body,
        )
        return _message_id(response)


def _recipient_type(target: PublishTarget) -> str:
    if target.kind == "group":
        return "group"
    if target.kind == "contact":
        return "individual"
    raise PublisherError(
        f"WhatsApp Cloud API cannot publish to a {target.kind} target; "
        "channels have no official API and need the OpenWA engine"
    )


def _validate_post(
    post: Post,
    target: PublishTarget,
    media_max_bytes: int,
) -> tuple[MediaFile, ...]:
    _recipient_type(target)
    if not target.key:
        raise PublisherError("WhatsApp Cloud target has an empty recipient id")

    caption = post.caption or ""
    if len(caption) > WHATSAPP_TEXT_LIMIT:
        raise PublisherError(f"WhatsApp text exceeds {WHATSAPP_TEXT_LIMIT} characters")

    media_files = tuple(sorted(post.media_files, key=lambda media: media.position))
    if not caption and not media_files:
        raise PublisherError("WhatsApp publication requires text, photo or video")

    for media in media_files:
        path = media_path(media)
        if not path.is_file():
            raise PublisherError(f"WhatsApp media file does not exist: {path.name}")
        size = path.stat().st_size
        suffix = path.suffix.lower()
        if media.media_type == "photo":
            if suffix not in SUPPORTED_PHOTO_SUFFIXES:
                raise PublisherError("WhatsApp Cloud photos must use JPEG or PNG")
            limit = min(CLOUD_PHOTO_MAX_SIZE, media_max_bytes)
        elif media.media_type == "video":
            if suffix not in SUPPORTED_VIDEO_SUFFIXES:
                raise PublisherError("WhatsApp Cloud videos must use MP4 or 3GP")
            limit = min(CLOUD_VIDEO_MAX_SIZE, media_max_bytes)
        else:
            raise PublisherError(f"Unsupported WhatsApp media type: {media.media_type}")
        if size > limit:
            raise PublisherError(
                f"WhatsApp Cloud {media.media_type} must not exceed "
                f"{limit // 1024**2} MB"
            )
    return media_files


def _content_type(path: Path, media_type: str) -> str:
    known_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
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


def _message_id(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            value = first.get("id")
            if isinstance(value, str) and value:
                return value
    raise PublisherError("WhatsApp Cloud API returned no message id")


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
        # Part of the post is already delivered; a retry would duplicate it.
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
