from __future__ import annotations

import json
from typing import Any

import aiohttp

from app.publishers.base import PublisherError

RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
# Transient Graph API errors: unknown/temporary failures and rate limiting.
RETRYABLE_GRAPH_CODES = {
    1,  # unknown error
    2,  # service temporarily unavailable
    4,  # application request limit reached
    17,  # user request limit reached
    32,  # page request limit reached
    341,  # application limit reached
    613,  # calls to this api have exceeded the rate limit
    9007,  # Instagram media publish is temporarily unavailable
    80001,  # rate limit for Instagram publishing
}


class GraphAPIError(RuntimeError):
    def __init__(
        self,
        status: int,
        message: str,
        code: int | None = None,
        subcode: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        details = f" (code {code})" if code is not None else ""
        super().__init__(f"Graph API HTTP {status}{details}: {message}")
        self.status = status
        self.code = code
        self.subcode = subcode
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        if self.code in RETRYABLE_GRAPH_CODES:
            return True
        return self.status in RETRYABLE_HTTP_STATUSES


async def request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    access_token: str,
    **kwargs: Any,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    headers.update(kwargs.pop("headers", None) or {})
    response = await session.request(method, url, headers=headers, **kwargs)
    try:
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError):
            payload = None

        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            raise GraphAPIError(
                response.status,
                _string(error, "message") or "unknown Graph API error",
                code=_integer(error, "code"),
                subcode=_integer(error, "error_subcode"),
                retry_after=retry_after(response),
            )
        if not 200 <= response.status < 300:
            raise GraphAPIError(
                response.status,
                await response_error_message(response),
                retry_after=retry_after(response),
            )
        if not isinstance(payload, dict):
            raise GraphAPIError(response.status, "invalid JSON response")
        return payload
    finally:
        response.release()


async def response_error_message(response: aiohttp.ClientResponse) -> str:
    text = (await response.text()).strip()
    return text[:500] if text else "empty response"


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
        raise PublisherError(f"Graph API response contains no {key}")
    return value


def safe_error(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def _string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _integer(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
