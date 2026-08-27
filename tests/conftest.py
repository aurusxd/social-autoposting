from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppConfig, PublishTarget
from app.database.models import Base
from app.services import media_storage
from app.services.dispatch_service import DispatchResult
from app.web.main import create_app
from app.web.routers import api
from app.web.security import SESSION_COOKIE
from tests.factories import PASSWORD, app_config

TARGETS = (
    PublishTarget("telegram", "-1001", "channel", "Основной канал"),
    PublishTarget("instagram", "self", "feed", "Instagram · Лента"),
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, ...]]:
    """Capture Celery dispatches instead of reaching for a real broker."""
    calls: list[tuple[int, ...]] = []

    def fake_dispatch(job_ids: tuple[int, ...]) -> DispatchResult:
        calls.append(tuple(job_ids))
        return DispatchResult(tuple(job_ids), ())

    monkeypatch.setattr(api, "dispatch_jobs", fake_dispatch)
    return calls


@pytest.fixture
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(media_storage, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(media_storage, "MEDIA_ROOT", root)
    return root


@pytest.fixture
def config() -> AppConfig:
    return app_config(targets=TARGETS)


@pytest.fixture
def client(
    config: AppConfig,
    session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    application = create_app(config=config, session_factory=session_factory)
    with TestClient(application, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    response = client.post(
        "/login",
        data={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 303, response.text
    assert client.cookies.get(SESSION_COOKIE)
    return client
