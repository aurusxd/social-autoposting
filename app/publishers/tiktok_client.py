from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import aiohttp
from loguru import logger

from app.publishers.base import PublisherError

RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
RETRYABLE_ERROR_CODES = {
    "rate_limit_exceeded",
    "internal_error",
    "server_error",
    "service_unavailable",
}
# TikTok rejects a stored refresh token that was rotated behind our back.
INVALID_GRANT_CODES = {"invalid_grant", "invalid_request", "access_token_invalid"}
TOKEN_EXPIRY_MARGIN_SECONDS = 300


class TikTokAPIError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"TikTok API HTTP {status} ({code}): {message}")
        self.status = status
        self.code = code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        if self.code in RETRYABLE_ERROR_CODES:
            return True
        return self.status in RETRYABLE_HTTP_STATUSES


@dataclass(frozen=True, slots=True)
class StoredToken:
    access_token: str
    refresh_token: str
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None

    def is_fresh(self, now: datetime | None = None) -> bool:
        if not self.access_token or self.expires_at is None:
            return False
        moment = now or datetime.now(UTC)
        return self.expires_at - timedelta(seconds=TOKEN_EXPIRY_MARGIN_SECONDS) > moment


class TokenStore(Protocol):
    def load(self, provider: str) -> StoredToken | None: ...

    def save(self, provider: str, token: StoredToken) -> None: ...


class MemoryTokenStore:
    """In-process token store; used in tests and when no database is wired."""

    def __init__(self, tokens: dict[str, StoredToken] | None = None) -> None:
        self.tokens = tokens or {}

    def load(self, provider: str) -> StoredToken | None:
        return self.tokens.get(provider)

    def save(self, provider: str, token: StoredToken) -> None:
        self.tokens[provider] = token


class TikTokTokenProvider:
    """Keeps a valid TikTok user access token, rotating the refresh token."""

    provider = "tiktok"

    def __init__(
        self,
        *,
        client_key: str,
        client_secret: str,
        refresh_token: str,
        api_base_url: str,
        store: TokenStore | None = None,
    ) -> None:
        self.client_key = client_key
        self.client_secret = client_secret
        self.configured_refresh_token = refresh_token
        self.api_base_url = api_base_url.rstrip("/")
        self.store = store or MemoryTokenStore()

    async def access_token(self, session: aiohttp.ClientSession) -> str:
        stored = self.store.load(self.provider)
        if stored is not None and stored.is_fresh():
            return stored.access_token

        refresh_token = (
            stored.refresh_token if stored else ""
        ) or self.configured_refresh_token
        if not refresh_token:
            raise PublisherError("TikTok refresh token is not configured")

        try:
            token = await self._refresh(session, refresh_token)
        except TikTokAPIError as error:
            fallback = self.configured_refresh_token
            if error.code not in INVALID_GRANT_CODES or refresh_token == fallback:
                raise
            logger.warning(
                "Stored TikTok refresh token was rejected ({}); "
                "retrying with TIKTOK_REFRESH_TOKEN",
                error.code,
            )
            token = await self._refresh(session, fallback)

        self.store.save(self.provider, token)
        return token.access_token

    async def _refresh(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
    ) -> StoredToken:
        payload = await oauth_request(
            session,
            f"{self.api_base_url}/v2/oauth/token/",
            {
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        # TikTok may hand back a brand new refresh token; the old one then dies.
        rotated = payload.get("refresh_token")
        now = datetime.now(UTC)
        return StoredToken(
            access_token=_required_string(payload, "access_token"),
            refresh_token=(
                rotated if isinstance(rotated, str) and rotated else refresh_token
            ),
            expires_at=_expires_at(payload, "expires_in", now),
            refresh_expires_at=_expires_at(payload, "refresh_expires_in", now),
        )


async def oauth_request(
    session: aiohttp.ClientSession,
    url: str,
    form: dict[str, str],
) -> dict[str, Any]:
    """Call the OAuth endpoint, which uses a flat error envelope of its own."""
    response = await session.post(
        url,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        payload = await _payload(response)
        code = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(code, str) and code and code != "ok":
            description = payload.get("error_description")
            raise TikTokAPIError(
                response.status,
                code,
                description if isinstance(description, str) else code,
                _retry_after(response),
            )
        if not 200 <= response.status < 300:
            raise TikTokAPIError(
                response.status,
                "http_error",
                await _response_text(response),
                _retry_after(response),
            )
        if not isinstance(payload, dict):
            raise TikTokAPIError(response.status, "invalid_response", "invalid JSON")
        return payload
    finally:
        response.release()


async def request_data(
    session: aiohttp.ClientSession,
    url: str,
    *,
    access_token: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST to an open.tiktokapis.com endpoint and return its `data` object."""
    response = await session.post(
        url,
        json=json_body if json_body is not None else {},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
    )
    try:
        payload = await _payload(response)
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code and code != "ok":
                message = error.get("message")
                raise TikTokAPIError(
                    response.status,
                    code,
                    message if isinstance(message, str) and message else code,
                    _retry_after(response),
                )
        if not 200 <= response.status < 300:
            raise TikTokAPIError(
                response.status,
                "http_error",
                await _response_text(response),
                _retry_after(response),
            )
        if not isinstance(payload, dict):
            raise TikTokAPIError(response.status, "invalid_response", "invalid JSON")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
    finally:
        response.release()


async def upload_chunk(
    session: aiohttp.ClientSession,
    upload_url: str,
    *,
    chunk: bytes,
    first_byte: int,
    total_size: int,
    content_type: str,
) -> None:
    last_byte = first_byte + len(chunk) - 1
    response = await session.put(
        upload_url,
        data=chunk,
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {first_byte}-{last_byte}/{total_size}",
        },
    )
    try:
        if not 200 <= response.status < 300:
            raise TikTokAPIError(
                response.status,
                "upload_failed",
                await _response_text(response),
                _retry_after(response),
            )
        await response.read()
    finally:
        response.release()


def required_data_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PublisherError(f"TikTok response contains no {key}")
    return value


def safe_error(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


async def _payload(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except (aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


async def _response_text(response: aiohttp.ClientResponse) -> str:
    text = (await response.text()).strip()
    return text[:500] if text else "empty response"


def _retry_after(response: aiohttp.ClientResponse) -> int | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        delay = int(value)
    except ValueError:
        return None
    return delay if delay > 0 else None


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PublisherError(f"TikTok OAuth response contains no {key}")
    return value


def _expires_at(payload: dict[str, Any], key: str, now: datetime) -> datetime | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return now + timedelta(seconds=seconds) if seconds > 0 else None
