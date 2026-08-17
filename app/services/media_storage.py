from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from aiogram import Bot

from app.bot.models import PostDraft

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEDIA_ROOT = PROJECT_ROOT / "media"


async def download_telegram_media(
    bot: Bot,
    file_id: str,
    suffix: str,
) -> str:
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    relative_path = Path("media") / f"{uuid4().hex}{normalized_suffix.lower()}"
    absolute_path = PROJECT_ROOT / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        await bot.download(file_id, destination=absolute_path)
    except Exception:
        absolute_path.unlink(missing_ok=True)
        raise

    return relative_path.as_posix()


def delete_draft_media(draft: PostDraft) -> None:
    media_root = MEDIA_ROOT.resolve()
    for media in draft.media:
        if not media.file_path:
            continue
        candidate = (PROJECT_ROOT / media.file_path).resolve()
        if candidate.is_relative_to(media_root):
            candidate.unlink(missing_ok=True)
