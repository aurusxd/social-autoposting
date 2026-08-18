from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo

from app.core.config import TelegramAPIConfig
from app.publishers.base import (
    MediaFile,
    Post,
    PublisherError,
    PublishResult,
    PublishTarget,
)
from app.services.telegram_client import build_telegram_bot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_LIMIT = 4096


class TelegramPublisher:
    platform = "telegram"

    def __init__(self, bot_token: str, api_config: TelegramAPIConfig) -> None:
        self.bot_token = bot_token
        self.api_config = api_config

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        bot = build_telegram_bot(self.bot_token, self.api_config)
        try:
            message_ids = await self._send_post(bot, post, target)
        except (TelegramNetworkError, TelegramRetryAfter, TelegramServerError) as error:
            return PublishResult(success=False, retryable=True, error=str(error))
        except TelegramAPIError as error:
            return PublishResult(success=False, retryable=False, error=str(error))
        finally:
            await bot.session.close()

        return PublishResult(
            success=True,
            external_id=",".join(str(message_id) for message_id in message_ids),
        )

    async def _send_post(
        self,
        bot: Bot,
        post: Post,
        target: PublishTarget,
    ) -> list[int]:
        chat_id = _chat_id(target.key)
        caption = post.caption or ""
        media_files = tuple(sorted(post.media_files, key=lambda item: item.position))

        if not media_files:
            return await _send_text(bot, chat_id, caption)

        inline_caption = caption if len(caption) <= TELEGRAM_CAPTION_LIMIT else ""
        if len(media_files) == 1:
            message_id = await _send_single_media(
                bot,
                chat_id,
                media_files[0],
                inline_caption,
            )
            message_ids = [message_id]
        else:
            media_group = []
            for index, media in enumerate(media_files):
                media_group.append(
                    _input_media(
                        media,
                        inline_caption if index == 0 else "",
                    )
                )
            messages = await bot.send_media_group(chat_id=chat_id, media=media_group)
            message_ids = [message.message_id for message in messages]

        if caption and not inline_caption:
            message_ids.extend(await _send_text(bot, chat_id, caption))
        return message_ids


async def _send_single_media(
    bot: Bot,
    chat_id: int | str,
    media: MediaFile,
    caption: str,
) -> int:
    source = _media_source(media)
    if media.media_type == "photo":
        message = await bot.send_photo(chat_id=chat_id, photo=source, caption=caption)
    elif media.media_type == "video":
        message = await bot.send_video(chat_id=chat_id, video=source, caption=caption)
    else:
        raise PublisherError(f"Unsupported media type: {media.media_type}")
    return message.message_id


def _input_media(
    media: MediaFile,
    caption: str,
) -> InputMediaPhoto | InputMediaVideo:
    source = _media_source(media)
    if media.media_type == "photo":
        return InputMediaPhoto(media=source, caption=caption)
    if media.media_type == "video":
        return InputMediaVideo(media=source, caption=caption)
    raise PublisherError(f"Unsupported media type: {media.media_type}")


def _media_source(media: MediaFile) -> str | FSInputFile:
    if media.tg_file_id:
        return media.tg_file_id
    return FSInputFile(PROJECT_ROOT / media.file_path)


async def _send_text(bot: Bot, chat_id: int | str, text: str) -> list[int]:
    if not text:
        raise PublisherError("A post without media must contain text")
    message_ids = []
    for start in range(0, len(text), TELEGRAM_TEXT_LIMIT):
        message = await bot.send_message(
            chat_id=chat_id,
            text=text[start : start + TELEGRAM_TEXT_LIMIT],
        )
        message_ids.append(message.message_id)
    return message_ids


def _chat_id(key: str) -> int | str:
    return int(key) if key.removeprefix("-").isdigit() else key
