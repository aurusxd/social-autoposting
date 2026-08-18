from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiohttp

from app.publishers.base import MediaFile, PublisherError

RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class ZernioHTTPError(RuntimeError):
    def __init__(
        self,
        status: int,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"Zernio HTTP {status}: {message}")
        self.status = status
        self.retry_after = retry_after


async def upload_media(
    session: aiohttp.ClientSession,
    *,
    api_key: str,
    api_base_url: str,
    media: MediaFile,
    path: Path,
    content_type: str,
) -> dict[str, str]:
    presign = await request_json(
        session,
        "POST",
        f"{api_base_url}/v1/media/presign",
        json={
            "filename": path.name,
            "contentType": content_type,
            "size": path.stat().st_size,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    upload_url = required_response_string(presign, "uploadUrl")
    public_url = required_response_string(presign, "publicUrl")

    with path.open("rb") as source:
        response = await session.put(
            upload_url,
            data=source,
            headers={"Content-Type": content_type},
        )
        try:
            if not 200 <= response.status < 300:
                message = await response_error_message(response)
                raise ZernioHTTPError(
                    response.status,
                    f"media upload failed: {message}",
                    retry_after(response),
                )
            await response.read()
        finally:
            response.release()

    return {
        "url": public_url,
        "type": "image" if media.media_type == "photo" else "video",
    }


async def request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any]:
    response = await session.request(method, url, **kwargs)
    try:
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if not 200 <= response.status < 300:
            message = error_message(payload) or await response_error_message(response)
            raise ZernioHTTPError(response.status, message, retry_after(response))
        if not isinstance(payload, dict):
            raise ZernioHTTPError(response.status, "invalid JSON response")
        return payload
    finally:
        response.release()


async def response_error_message(response: aiohttp.ClientResponse) -> str:
    text = (await response.text()).strip()
    return text[:500] if text else "empty response"


def error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("error", "message", "code"):
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


def required_response_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PublisherError(f"Zernio response contains no {key}")
    return value


def external_id(payload: dict[str, Any]) -> str:
    for key in ("post", "existingPost"):
        post = payload.get(key)
        if isinstance(post, dict):
            value = post.get("_id") or post.get("id")
            if value:
                return str(value)
    value = payload.get("_id") or payload.get("id")
    if value:
        return str(value)
    raise PublisherError("Zernio returned no post identifier")


def safe_error(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__
