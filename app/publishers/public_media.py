from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from app.publishers.base import MediaFile, PublisherError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def absolute_path(path: Path) -> Path:
    """Resolve a possibly project-relative path against the project root."""
    return path if path.is_absolute() else PROJECT_ROOT / path


def media_path(media: MediaFile) -> Path:
    return absolute_path(Path(media.file_path))


def public_url(
    path: Path,
    *,
    media_base_url: str,
    media_root: Path,
    platform: str,
) -> str:
    """Build the internet-facing URL an official API can download the file from."""
    try:
        relative_path = path.resolve().relative_to(media_root)
    except ValueError as error:
        raise PublisherError(
            f"{platform} media is outside configured media root: {path.name}"
        ) from error
    return f"{media_base_url.rstrip('/')}/{quote(relative_path.as_posix(), safe='/')}"
