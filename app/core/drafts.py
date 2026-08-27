from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

MediaType = Literal["photo", "video"]
MEDIA_LIMIT = 10


@dataclass(frozen=True, slots=True)
class DraftMedia:
    """One uploaded file that is already stored under the media directory."""

    file_path: str
    media_type: MediaType
    file_name: str = ""
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "media_type": self.media_type,
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DraftMedia:
        media_type = value["media_type"]
        if media_type not in {"photo", "video"}:
            raise ValueError(f"Unsupported media type: {media_type}")
        return cls(
            file_path=str(value["file_path"]),
            media_type=media_type,
            file_name=str(value.get("file_name", "")),
            size_bytes=int(value.get("size_bytes", 0)),
        )


@dataclass(frozen=True, slots=True)
class PostDraft:
    caption: str = ""
    media: tuple[DraftMedia, ...] = ()

    @property
    def has_content(self) -> bool:
        return bool(self.caption.strip() or self.media)

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption": self.caption,
            "media": [item.to_dict() for item in self.media],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> PostDraft:
        if not value:
            return cls()
        return cls(
            caption=str(value.get("caption", "")),
            media=tuple(DraftMedia.from_dict(item) for item in value.get("media", [])),
        )
