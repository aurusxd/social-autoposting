from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app.core.drafts import DraftMedia, MediaType, PostDraft

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEDIA_ROOT = PROJECT_ROOT / "media"

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp", ".webm"}


class MediaError(ValueError):
    """Raised when an upload cannot be accepted or stored."""


def media_type_for(file_name: str) -> MediaType:
    suffix = PurePosixPath(file_name.replace("\\", "/")).suffix.lower()
    if suffix in PHOTO_EXTENSIONS:
        return "photo"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    supported = ", ".join(sorted(PHOTO_EXTENSIONS | VIDEO_EXTENSIONS))
    raise MediaError(
        f"Формат {suffix or file_name} не поддерживается. Доступны: {supported}"
    )


async def save_upload(
    file_name: str,
    chunks: AsyncIterator[bytes],
    max_bytes: int,
    media_root: Path | None = None,
) -> DraftMedia:
    """Stream an uploaded file to disk and describe it as draft media.

    The name the browser sent is only used to pick the media type: the stored
    file gets a generated name so a hostile name cannot escape the directory.
    """
    display_name = PurePosixPath(file_name.replace("\\", "/")).name or "upload"
    media_type = media_type_for(display_name)
    suffix = PurePosixPath(display_name).suffix.lower()

    root = MEDIA_ROOT if media_root is None else media_root
    root.mkdir(parents=True, exist_ok=True)
    absolute_path = root / f"{uuid4().hex}{suffix}"
    size = 0
    try:
        with absolute_path.open("wb") as target:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise MediaError(
                        f"Файл больше допустимых {max_bytes // 1024**2} МБ"
                    )
                target.write(chunk)
    except BaseException:
        absolute_path.unlink(missing_ok=True)
        raise

    if size == 0:
        absolute_path.unlink(missing_ok=True)
        raise MediaError("Файл пустой")

    relative_path = absolute_path.relative_to(PROJECT_ROOT).as_posix()
    return DraftMedia(
        file_path=relative_path,
        media_type=media_type,
        file_name=display_name,
        size_bytes=size,
    )


def resolve_media_path(relative_path: str) -> Path | None:
    """Return the on-disk path of stored media, or None if it is not ours."""
    if not relative_path:
        return None
    candidate = (PROJECT_ROOT / relative_path).resolve()
    media_root = MEDIA_ROOT.resolve()
    if not candidate.is_relative_to(media_root) or not candidate.is_file():
        return None
    return candidate


def delete_media(relative_paths: Iterable[str]) -> None:
    for relative_path in relative_paths:
        candidate = resolve_media_path(relative_path)
        if candidate is not None:
            candidate.unlink(missing_ok=True)


def delete_draft_media(draft: PostDraft) -> None:
    delete_media(media.file_path for media in draft.media)
