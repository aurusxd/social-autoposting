from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.database.models import OAuthToken
from app.publishers.tiktok_client import StoredToken


class OAuthTokenRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, provider: str) -> StoredToken | None:
        record = self.session.get(OAuthToken, provider)
        if record is None or not record.refresh_token:
            return None
        return StoredToken(
            access_token=record.access_token or "",
            refresh_token=record.refresh_token,
            expires_at=_as_utc(record.expires_at),
            refresh_expires_at=_as_utc(record.refresh_expires_at),
        )

    def upsert(self, provider: str, token: StoredToken) -> None:
        record = self.session.get(OAuthToken, provider)
        if record is None:
            record = OAuthToken(provider=provider, refresh_token=token.refresh_token)
            self.session.add(record)
        record.access_token = token.access_token
        record.refresh_token = token.refresh_token
        record.expires_at = _as_naive(token.expires_at)
        record.refresh_expires_at = _as_naive(token.refresh_expires_at)


class DatabaseTokenStore:
    """Persists rotating OAuth tokens so a restart does not lose them."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def load(self, provider: str) -> StoredToken | None:
        with self.session_factory() as session:
            return OAuthTokenRepository(session).get(provider)

    def save(self, provider: str, token: StoredToken) -> None:
        with self.session_factory.begin() as session:
            OAuthTokenRepository(session).upsert(provider, token)


def _as_naive(moment: datetime | None) -> datetime | None:
    """SQLite keeps no offset, so normalise to naive UTC on the way in."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
