from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MediaFile:
    file_path: str
    media_type: str
    tg_file_id: str | None = None
    position: int = 0


@dataclass(frozen=True, slots=True)
class Post:
    id: int
    caption: str | None
    media_files: tuple[MediaFile, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishTarget:
    key: str
    kind: str
    name: str


@dataclass(frozen=True, slots=True)
class PublishResult:
    success: bool
    retryable: bool = False
    error: str | None = None
    external_id: str | None = None


class PublisherError(RuntimeError):
    """Raised when publishing cannot be retried safely."""


class Publisher(Protocol):
    platform: str

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult: ...
