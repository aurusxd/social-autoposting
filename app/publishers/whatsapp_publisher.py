from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from loguru import logger

from app.publishers.base import (
    MediaFile,
    Post,
    PublisherError,
    PublishResult,
    PublishTarget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WHATSAPP_TEXT_LIMIT = 4096
WHATSAPP_MEDIA_CAPTION_LIMIT = 1024
SUPPORTED_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".3gp"}
RETRYABLE_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenWAHTTPError(RuntimeError):
    def __init__(
        self,
        status: int,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"OpenWA HTTP {status}: {message}")
        self.status = status
        self.retry_after = retry_after


class WhatsAppPublisher:
    platform = "whatsapp"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        session_id: str,
        request_timeout: int = 120,
        media_base_url: str | None = None,
        media_root: str | Path = "media",
        media_max_bytes: int = 100 * 1024**2,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        self.request_timeout = request_timeout
        self.media_base_url = media_base_url.rstrip("/") if media_base_url else None
        self.media_root = _absolute_path(Path(media_root)).resolve()
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
                    sent_ids.append(_message_id(response))
                else:
                    media_caption = caption
                    if len(caption) > WHATSAPP_MEDIA_CAPTION_LIMIT:
                        response = await self._send_text(session, target.key, caption)
                        sent_ids.append(_message_id(response))
                        media_caption = ""

                    for index, media in enumerate(media_files):
                        response = await self._send_media(
                            session,
                            target.key,
                            media,
                            media_caption if index == 0 else "",
                        )
                        sent_ids.append(_message_id(response))
        except OpenWAHTTPError as error:
            return _failure_result(
                error,
                retryable=error.status in RETRYABLE_HTTP_STATUSES,
                retry_after=error.retry_after,
                sent_ids=sent_ids,
            )
        except (aiohttp.ClientError, TimeoutError) as error:
            return _failure_result(error, retryable=True, sent_ids=sent_ids)
        except (OSError, PublisherError, ValueError) as error:
            return _failure_result(error, retryable=False, sent_ids=sent_ids)
        except Exception as error:
            logger.exception("Unexpected WhatsApp/OpenWA publisher error")
            return _failure_result(error, retryable=True, sent_ids=sent_ids)

        return PublishResult(success=True, external_id=",".join(sent_ids))

    async def _send_text(
        self,
        session: aiohttp.ClientSession,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        return await self._request_json(
            session,
            "send-text",
            {"chatId": chat_id, "text": text},
        )

    async def _send_media(
        self,
        session: aiohttp.ClientSession,
        chat_id: str,
        media: MediaFile,
        caption: str,
    ) -> dict[str, Any]:
        path = _media_path(media)
        mimetype = _content_type(path, media.media_type)
        endpoint = "send-image" if media.media_type == "photo" else "send-video"
        payload: dict[str, Any] = {
            "chatId": chat_id,
            "filename": path.name,
            "mimetype": mimetype,
        }
        if caption:
            payload["caption"] = caption

        if self.media_base_url:
            try:
                relative_path = path.resolve().relative_to(self.media_root)
            except ValueError as error:
                raise PublisherError(
                    f"WhatsApp media is outside configured media root: {path.name}"
                ) from error
            encoded_path = quote(relative_path.as_posix(), safe="/")
            payload["url"] = f"{self.media_base_url}/{encoded_path}"
            try:
                return await self._request_json(session, endpoint, payload)
            except OpenWAHTTPError as error:
                if error.status != 400:
                    raise
                logger.warning(
                    "OpenWA rejected media URL; retrying the same file as Base64"
                )
                payload.pop("url")
                payload["base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
        else:
            payload["base64"] = base64.b64encode(path.read_bytes()).decode("ascii")

        return await self._request_json(session, endpoint, payload)

    async def _request_json(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = quote(self.session_id, safe="")
        response = await session.post(
            f"{self.api_url}/sessions/{session_id}/messages/{endpoint}",
            json=payload.copy(),
            headers={"X-API-Key": self.api_key},
        )
        try:
            try:
                body = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError):
                body = None
            if not 200 <= response.status < 300:
                message = _error_message(body) or (await response.text()).strip()
                raise OpenWAHTTPError(
                    response.status,
                    message[:500] if message else "empty response",
                    _retry_after(response, body),
                )
            if not isinstance(body, dict):
                raise OpenWAHTTPError(response.status, "invalid JSON response")
            return body
        finally:
            response.release()


def _validate_post(
    post: Post,
    target: PublishTarget,
    media_max_bytes: int,
) -> tuple[MediaFile, ...]:
    if target.kind == "group":
        if not target.key.endswith("@g.us"):
            raise PublisherError("WhatsApp group JID must end with @g.us")
    elif target.kind == "channel":
        if not target.key.endswith("@newsletter"):
            raise PublisherError("WhatsApp channel JID must end with @newsletter")
    else:
        raise PublisherError(f"Unsupported WhatsApp target kind: {target.kind}")

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
    return _absolute_path(Path(media.file_path))


def _absolute_path(path: Path) -> Path:
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


def _message_id(payload: dict[str, Any]) -> str:
    value = payload.get("messageId")
    if not isinstance(value, str) or not value:
        raise PublisherError("OpenWA returned no messageId")
    return value


def _error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("message", "error", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            messages = [str(item).strip() for item in value if str(item).strip()]
            if messages:
                return "; ".join(messages)
    return None


def _retry_after(
    response: aiohttp.ClientResponse,
    payload: Any,
) -> int | None:
    raw_header = response.headers.get("Retry-After")
    candidates = [raw_header]
    if isinstance(payload, dict):
        candidates.append(payload.get("retryAfterSeconds"))
    for value in candidates:
        try:
            delay = int(value) if value is not None else 0
        except (TypeError, ValueError):
            continue
        if delay > 0:
            return delay
    return None


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
