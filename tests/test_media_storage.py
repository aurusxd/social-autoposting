from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.services import media_storage
from app.services.media_storage import MediaError, media_type_for, save_upload


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("photo.JPG", "photo"),
        ("picture.png", "photo"),
        ("clip.mp4", "video"),
        ("clip.MOV", "video"),
    ],
)
def test_media_type_follows_the_extension(file_name: str, expected: str) -> None:
    assert media_type_for(file_name) == expected


@pytest.mark.parametrize("file_name", ["archive.zip", "notes.txt", "noextension"])
def test_unsupported_formats_are_refused(file_name: str) -> None:
    with pytest.raises(MediaError):
        media_type_for(file_name)


def test_upload_is_stored_under_the_media_directory(media_root: Path) -> None:
    media = asyncio.run(
        save_upload("cat.jpg", _stream(b"abc", b"def"), 1024, media_root)
    )

    stored = media_root / Path(media.file_path).name
    assert stored.read_bytes() == b"abcdef"
    assert media.media_type == "photo"
    assert media.file_name == "cat.jpg"
    assert media.size_bytes == 6
    assert media.file_path.startswith("media/")


def test_a_hostile_name_cannot_escape_the_media_directory(media_root: Path) -> None:
    media = asyncio.run(
        save_upload("../../etc/passwd.png", _stream(b"data"), 1024, media_root)
    )

    stored = (media_root.parent / media.file_path).resolve()
    assert stored.parent == media_root.resolve()
    assert "passwd" not in stored.name


def test_an_oversized_upload_is_rejected_and_leaves_no_file(media_root: Path) -> None:
    with pytest.raises(MediaError, match="больше"):
        asyncio.run(save_upload("big.mp4", _stream(b"x" * 50), 10, media_root))

    assert list(media_root.iterdir()) == []


def test_an_empty_upload_is_rejected(media_root: Path) -> None:
    with pytest.raises(MediaError, match="пустой"):
        asyncio.run(save_upload("empty.jpg", _stream(), 1024, media_root))

    assert list(media_root.iterdir()) == []


def test_stored_media_resolves_only_inside_the_media_directory(
    media_root: Path,
) -> None:
    media = asyncio.run(save_upload("cat.jpg", _stream(b"data"), 1024, media_root))
    outsider = media_root.parent / "secret.txt"
    outsider.write_text("keep out", encoding="utf-8")

    assert media_storage.resolve_media_path(media.file_path) is not None
    assert media_storage.resolve_media_path("secret.txt") is None
    assert media_storage.resolve_media_path("media/../secret.txt") is None
    assert media_storage.resolve_media_path("") is None


def test_deleting_media_removes_only_our_files(media_root: Path) -> None:
    media = asyncio.run(save_upload("cat.jpg", _stream(b"data"), 1024, media_root))
    outsider = media_root.parent / "secret.txt"
    outsider.write_text("keep out", encoding="utf-8")

    media_storage.delete_media([media.file_path, "secret.txt"])

    assert list(media_root.iterdir()) == []
    assert outsider.exists()
