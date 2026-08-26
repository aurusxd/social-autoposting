from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from app.core.config import (
    AppConfig,
    PublishTarget,
    TelegramAPIConfig,
    WhatsAppConfig,
)
from app.services import target_registry
from app.services.target_registry import resolve_targets

GROUPS = {
    "groups": [
        {"id": "1203630001@g.us", "name": "Клиенты"},
        {"id": "1203630002@g.us", "name": "Партнёры"},
        {"id": "", "name": "Без идентификатора"},
    ]
}
NEWSLETTERS = {
    "newsletters": [
        {"id": "1203630003@newsletter", "name": "Наш канал", "role": "owner"},
        {"id": "1203630004@newsletter", "name": "Второй", "role": "admin"},
        {"id": "1203630005@newsletter", "name": "Чужой", "role": "subscriber"},
        {"id": "1203630006@newsletter", "name": "Гостевой", "role": "guest"},
    ]
}


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any] | None) -> None:
        self.status = status
        self.payload = payload
        self.headers: dict[str, str] = {}

    async def json(self, **_: Any) -> dict[str, Any] | None:
        return self.payload

    async def text(self) -> str:
        return ""

    def release(self) -> None:
        return None


class FakeSession:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.urls: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.urls.append(url)
        self.params.append(kwargs.get("params", {}))
        if self.status != 200:
            return FakeResponse(self.status, {"error": {"message": "nope"}})
        payload = GROUPS if url.endswith("/groups") else NEWSLETTERS
        return FakeResponse(200, payload)


def _session_factory(session: FakeSession):
    def factory(**kwargs: Any) -> FakeSession:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        return session

    return factory


def _config(target_limit: int = 50, whatsapp: bool = True) -> AppConfig:
    return AppConfig(
        bot_token="token",
        owner_id=1,
        telegram_api=TelegramAPIConfig(
            base_url="https://api.telegram.org",
            local=False,
            server_files_path=Path("/tmp"),
            client_files_path=Path("/tmp"),
        ),
        targets=(PublishTarget("telegram", "-1001", "channel", "Основной"),),
        whatsapp=(
            WhatsAppConfig(
                api_token="whapi-token",
                api_url="https://gate.whapi.example",
                request_timeout=30,
                media_max_bytes=100 * 1024**2,
                target_limit=target_limit,
            )
            if whatsapp
            else None
        ),
        instagram=None,
        tiktok=None,
    )


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    target_registry.clear_cache()


def test_groups_and_admin_channels_are_merged_with_static_targets() -> None:
    session = FakeSession()

    resolved = asyncio.run(
        resolve_targets(_config(), session_factory=_session_factory(session))
    )

    assert [(t.platform, t.kind, t.name) for t in resolved.targets] == [
        ("telegram", "channel", "Основной"),
        ("whatsapp", "channel", "Наш канал"),
        ("whatsapp", "channel", "Второй"),
        ("whatsapp", "group", "Клиенты"),
        ("whatsapp", "group", "Партнёры"),
    ]
    assert not resolved.whatsapp_failed


def test_truncation_never_drops_every_channel() -> None:
    session = FakeSession()

    resolved = asyncio.run(
        resolve_targets(
            _config(target_limit=1),
            session_factory=_session_factory(session),
        )
    )

    kinds = [t.kind for t in resolved.targets if t.platform == "whatsapp"]
    # Groups are many and channels are few, so channels must survive the cut.
    assert kinds == ["channel"]
    assert resolved.truncated


def test_the_fetch_asks_for_no_more_than_the_limit() -> None:
    session = FakeSession()

    asyncio.run(
        resolve_targets(
            _config(target_limit=7),
            session_factory=_session_factory(session),
        )
    )

    assert session.params == [{"count": 7}, {"count": 7}]


def test_channels_without_posting_rights_are_skipped() -> None:
    session = FakeSession()

    resolved = asyncio.run(
        resolve_targets(_config(), session_factory=_session_factory(session))
    )

    names = {target.name for target in resolved.targets}
    # A subscriber or a guest cannot post into a channel.
    assert "Чужой" not in names
    assert "Гостевой" not in names


def test_entries_without_an_id_are_skipped() -> None:
    session = FakeSession()

    resolved = asyncio.run(
        resolve_targets(_config(), session_factory=_session_factory(session))
    )

    assert all(target.key for target in resolved.targets)


def test_whatsapp_failure_keeps_the_other_platforms() -> None:
    session = FakeSession(status=500)

    resolved = asyncio.run(
        resolve_targets(_config(), session_factory=_session_factory(session))
    )

    assert resolved.whatsapp_failed
    assert [t.platform for t in resolved.targets] == ["telegram"]


def test_target_limit_truncates_and_flags() -> None:
    session = FakeSession()

    resolved = asyncio.run(
        resolve_targets(
            _config(target_limit=2),
            session_factory=_session_factory(session),
        )
    )

    assert resolved.truncated
    assert len(resolved.targets) == 1 + 2


def test_disabled_whatsapp_makes_no_requests() -> None:
    session = FakeSession()

    resolved = asyncio.run(
        resolve_targets(
            _config(whatsapp=False),
            session_factory=_session_factory(session),
        )
    )

    assert not session.urls
    assert [t.platform for t in resolved.targets] == ["telegram"]


def test_second_call_is_served_from_cache() -> None:
    session = FakeSession()
    config = _config()
    factory = _session_factory(session)

    asyncio.run(resolve_targets(config, session_factory=factory))
    asyncio.run(resolve_targets(config, session_factory=factory))

    assert len(session.urls) == 2


def test_refresh_bypasses_the_cache() -> None:
    session = FakeSession()
    config = _config()
    factory = _session_factory(session)

    asyncio.run(resolve_targets(config, session_factory=factory))
    asyncio.run(resolve_targets(config, session_factory=factory, refresh=True))

    assert len(session.urls) == 4


def test_cache_expires_after_the_ttl() -> None:
    session = FakeSession()
    config = _config()
    factory = _session_factory(session)
    # First call stores the entry, the second one checks its age and refetches.
    clock = iter([0.0, 1000.0, 1000.0])

    asyncio.run(
        resolve_targets(config, session_factory=factory, now=lambda: next(clock))
    )
    asyncio.run(
        resolve_targets(config, session_factory=factory, now=lambda: next(clock))
    )

    assert len(session.urls) == 4


def test_stub_newsletters_response_yields_no_channels() -> None:
    class StubNewsletters(FakeSession):
        async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            self.params.append(kwargs.get("params", {}))
            if url.endswith("/newsletters"):
                # What the live API returns when the account has no channels.
                return FakeResponse(200, {"code": 200})
            return FakeResponse(200, GROUPS)

    session = StubNewsletters()

    resolved = asyncio.run(
        resolve_targets(_config(), session_factory=_session_factory(session))
    )

    assert not resolved.whatsapp_failed
    assert [t.kind for t in resolved.targets if t.platform == "whatsapp"] == [
        "group",
        "group",
    ]
