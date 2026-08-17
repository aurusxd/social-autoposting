from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

MediaType = Literal["photo", "video"]


@dataclass(frozen=True, slots=True)
class DraftMedia:
    file_id: str
    media_type: MediaType

    def to_dict(self) -> dict[str, str]:
        return {"file_id": self.file_id, "media_type": self.media_type}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DraftMedia:
        return cls(file_id=str(value["file_id"]), media_type=value["media_type"])


@dataclass(frozen=True, slots=True)
class PostDraft:
    caption: str = ""
    media: tuple[DraftMedia, ...] = ()

    @property
    def has_content(self) -> bool:
        return bool(self.caption.strip() or self.media)

    def append_caption(self, text: str | None) -> PostDraft:
        normalized = (text or "").strip()
        if not normalized:
            return self
        caption = f"{self.caption}\n\n{normalized}" if self.caption else normalized
        return replace(self, caption=caption)

    def append_media(self, item: DraftMedia, limit: int = 10) -> PostDraft:
        if len(self.media) >= limit:
            raise ValueError(f"A draft can contain at most {limit} media files")
        return replace(self, media=(*self.media, item))

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


def toggle_index(selected: set[int], index: int, total: int) -> set[int]:
    if index < 0 or index >= total:
        raise IndexError("Target index is out of range")
    updated = set(selected)
    if index in updated:
        updated.remove(index)
    else:
        updated.add(index)
    return updated
