from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiohttp

from app.publishers.base import PublisherError

RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class WhapiHTTPError(RuntimeError):
    def __init__(
        self,
        status: int,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"Whapi HTTP {status}: {message}")
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status in RETRYABLE_HTTP_STATUSES


async def request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    token: str,
    **kwargs: Any,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(kwargs.pop("headers", None) or {})
    response = await session.request(method, url, headers=headers, **kwargs)
    try:
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if not 200 <= response.status < 300:
            message = error_message(payload) or await response_error_message(response)
            # The error text reaches the database and Telegram, so scrub the token
            # in case an upstream proxy echoed the request back to us.
            raise WhapiHTTPError(
                response.status,
                redact(message, token),
                retry_after(response),
            )
        if not isinstance(payload, dict):
            raise WhapiHTTPError(response.status, "invalid JSON response")
        return payload
    finally:
        response.release()


async def send_media(
    session: aiohttp.ClientSession,
    url: str,
    *,
    token: str,
    path: Path,
    to: str,
    caption: str,
    content_type: str,
) -> dict[str, Any]:
    """Upload the file straight to Whapi; no public media URL is involved."""
    form = aiohttp.FormData()
    form.add_field("to", to)
    if caption:
        form.add_field("caption", caption)
    form.add_field(
        "media",
        path.read_bytes(),
        filename=path.name,
        content_type=content_type,
    )
    return await request_json(session, "POST", url, token=token, data=form)


async def response_error_message(response: aiohttp.ClientResponse) -> str:
    text = (await response.text()).strip()
    return text[:500] if text else "empty response"


def error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "details", "code"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("message", "error", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def retry_after(response: aiohttp.ClientResponse) -> int | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        delay = int(value)
    except ValueError:
        return None
    return delay if delay > 0 else None


def message_id(payload: dict[str, Any]) -> str:
    """Whapi answers with {"sent": true, "message": {"id": ...}}."""
    if payload.get("sent") is False:
        # A message object may still be present, so never trust the id alone.
        raise PublisherError("Whapi accepted the request but did not send it")

    message = payload.get("message")
    if isinstance(message, dict):
        value = message.get("id")
        if isinstance(value, str) and value:
            return value
    value = payload.get("id")
    if isinstance(value, str) and value:
        return value
    raise PublisherError("Whapi returned no message id")


def redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def safe_error(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__
