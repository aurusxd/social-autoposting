from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiohttp
from loguru import logger

from app.core.config import AppConfig, PublishTarget, WhatsAppConfig
from app.publishers.whapi_client import request_json

CACHE_TTL_SECONDS = 60
# Only an owner or an admin may post into a WhatsApp channel.
POSTING_ROLES = {"owner", "admin"}


@dataclass(frozen=True, slots=True)
class ResolvedTargets:
    targets: tuple[PublishTarget, ...]
    whatsapp_failed: bool = False
    truncated: bool = False


@dataclass(slots=True)
class _CacheEntry:
    targets: tuple[PublishTarget, ...]
    truncated: bool
    stored_at: float


_cache: dict[str, _CacheEntry] = {}


def clear_cache() -> None:
    _cache.clear()


async def resolve_targets(
    config: AppConfig,
    *,
    refresh: bool = False,
    session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
    now: Callable[[], float] = time.monotonic,
) -> ResolvedTargets:
    """Merge the configured targets with the chats Whapi reports right now."""
    static_targets = tuple(
        target for target in config.targets if target.platform != "whatsapp"
    )
    if config.whatsapp is None:
        return ResolvedTargets(static_targets)

    cached = None if refresh else _cached(config.whatsapp.api_url, now)
    if cached is not None:
        return ResolvedTargets(
            (*static_targets, *cached.targets),
            truncated=cached.truncated,
        )

    try:
        whatsapp_targets, truncated = await _fetch_whatsapp_targets(
            config.whatsapp,
            session_factory,
        )
    except Exception as error:
        # WhatsApp being down must not hide Telegram or Instagram from the user.
        logger.warning("Could not load WhatsApp chats from Whapi: {}", error)
        return ResolvedTargets(static_targets, whatsapp_failed=True)

    _cache[config.whatsapp.api_url] = _CacheEntry(
        targets=whatsapp_targets,
        truncated=truncated,
        stored_at=now(),
    )
    return ResolvedTargets(
        (*static_targets, *whatsapp_targets),
        truncated=truncated,
    )


def _cached(api_url: str, now: Callable[[], float]) -> _CacheEntry | None:
    entry = _cache.get(api_url)
    if entry is None:
        return None
    if now() - entry.stored_at > CACHE_TTL_SECONDS:
        del _cache[api_url]
        return None
    return entry


async def _fetch_whatsapp_targets(
    config: WhatsAppConfig,
    session_factory: Callable[..., aiohttp.ClientSession],
) -> tuple[tuple[PublishTarget, ...], bool]:
    timeout = aiohttp.ClientTimeout(total=config.request_timeout)
    async with session_factory(timeout=timeout) as session:
        groups = await request_json(
            session,
            "GET",
            f"{config.api_url}/groups",
            token=config.api_token,
            params={"count": config.target_limit},
        )
        newsletters = await request_json(
            session,
            "GET",
            f"{config.api_url}/newsletters",
            token=config.api_token,
            params={"count": config.target_limit},
        )

    # Channels come first: an account is admin of few of them, while groups can
    # be numerous, and truncating the tail must never drop every channel.
    targets = [
        *_newsletters_to_targets(newsletters),
        *_groups_to_targets(groups),
    ]
    truncated = len(targets) > config.target_limit
    return tuple(targets[: config.target_limit]), truncated


def _groups_to_targets(payload: dict[str, Any]) -> list[PublishTarget]:
    targets = []
    for item in _items(payload, "groups"):
        chat_id = _text(item, "id")
        if not chat_id:
            continue
        targets.append(
            PublishTarget(
                platform="whatsapp",
                key=chat_id,
                kind="group",
                name=_text(item, "name") or chat_id,
            )
        )
    return targets


def _newsletters_to_targets(payload: dict[str, Any]) -> list[PublishTarget]:
    if "newsletters" not in payload:
        # Whapi answers with a bare {"code": 200} when the account has no
        # channels at all — notably for numbers in regions where WhatsApp does
        # not offer Channels. Groups still work, so this is not a failure.
        logger.info(
            "Whapi reported no WhatsApp channels for this account; "
            "only groups will be offered"
        )
        return []

    targets = []
    for item in _items(payload, "newsletters"):
        chat_id = _text(item, "id")
        if not chat_id:
            continue
        if (_text(item, "role") or "").lower() not in POSTING_ROLES:
            continue
        targets.append(
            PublishTarget(
                platform="whatsapp",
                key=chat_id,
                kind="channel",
                name=_text(item, "name") or chat_id,
            )
        )
    return targets


def _items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    return value.strip() if isinstance(value, str) else ""
