from __future__ import annotations

import asyncio
from html import escape
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import AiogramError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from app.bot.keyboards import (
    DraftAction,
    ReviewAction,
    TargetAction,
    draft_keyboard,
    review_keyboard,
    targets_keyboard,
)
from app.bot.models import (
    DraftMedia,
    PostDraft,
    remap_selection,
    targets_from_data,
    targets_to_data,
    toggle_index,
)
from app.bot.states import BotFlow
from app.bot.views import draft_text, review_text, targets_text, welcome_text
from app.core.config import AppConfig
from app.services.dispatch_service import dispatch_jobs
from app.services.media_storage import delete_draft_media, download_telegram_media
from app.services.submission_service import SubmissionError, save_submission
from app.services.target_registry import resolve_targets

router = Router(name="telegram-ui")


@router.message(Command("start", "new"))
async def start_draft(message: Message, state: FSMContext) -> None:
    await _delete_current_draft_media(state)
    await state.clear()
    await state.set_state(BotFlow.collecting)
    await message.answer(welcome_text())


@router.message(Command("cancel"))
async def cancel_draft(message: Message, state: FSMContext) -> None:
    await _delete_current_draft_media(state)
    await state.clear()
    await message.answer("Черновик удалён. Отправьте /new, чтобы начать заново.")


@router.message(BotFlow.selecting_targets, F.text | F.photo | F.video)
@router.message(BotFlow.reviewing, F.text | F.photo | F.video)
async def content_during_selection(message: Message) -> None:
    await message.answer("Сначала завершите текущий выбор или нажмите «Отмена».")


@router.message(BotFlow.collecting, F.text | F.photo | F.video)
async def collect_content(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    draft = PostDraft.from_dict(data.get("draft"))

    if message.photo:
        if len(draft.media) >= 10:
            await message.answer("В одном посте можно добавить не больше 10 файлов.")
            return
        try:
            photo = message.photo[-1]
            file_path = await download_telegram_media(
                message.bot,
                photo.file_id,
                ".jpg",
            )
            draft = draft.append_media(DraftMedia(photo.file_id, "photo", file_path))
        except (AiogramError, OSError) as error:
            await message.answer(f"Не удалось сохранить фото: {escape(str(error))}")
            return
        draft = draft.append_caption(message.caption)
    elif message.video:
        if len(draft.media) >= 10:
            await message.answer("В одном посте можно добавить не больше 10 файлов.")
            return
        try:
            suffix = Path(message.video.file_name or "video.mp4").suffix or ".mp4"
            file_path = await download_telegram_media(
                message.bot,
                message.video.file_id,
                suffix,
            )
            draft = draft.append_media(
                DraftMedia(message.video.file_id, "video", file_path)
            )
        except (AiogramError, OSError) as error:
            await message.answer(f"Не удалось сохранить видео: {escape(str(error))}")
            return
        draft = draft.append_caption(message.caption)
    elif message.text and not message.text.startswith("/"):
        draft = draft.append_caption(message.text)
    else:
        return

    await state.update_data(draft=draft.to_dict(), selected=[])
    await _show_or_refresh_draft(message, state, draft)


@router.message(F.text | F.photo | F.video)
async def start_from_content(message: Message, state: FSMContext) -> None:
    await state.set_state(BotFlow.collecting)
    await collect_content(message, state)


@router.callback_query(DraftAction.filter(F.action == "targets"))
async def open_targets(
    callback: CallbackQuery,
    state: FSMContext,
    app_config: AppConfig,
) -> None:
    data = await state.get_data()
    draft = PostDraft.from_dict(data.get("draft"))
    if not draft.has_content:
        await callback.answer("Сначала добавьте контент", show_alert=True)
        return
    resolved = await resolve_targets(app_config)
    if not resolved.targets:
        await callback.answer("Нет доступных площадок", show_alert=True)
        return

    # The chat list may have changed since the previous visit, so move the
    # selection across by identity instead of trusting the old indexes.
    selected = remap_selection(
        targets_from_data(data.get("targets")),
        _selected_from_data(data),
        resolved.targets,
    )
    await state.update_data(
        targets=targets_to_data(resolved.targets),
        selected=sorted(selected),
    )
    await state.set_state(BotFlow.selecting_targets)
    await _edit_callback_message(
        callback,
        targets_text(len(selected), resolved),
        targets_keyboard(resolved.targets, selected),
    )
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "refresh"))
async def refresh_targets(
    callback: CallbackQuery,
    state: FSMContext,
    app_config: AppConfig,
) -> None:
    data = await state.get_data()
    previous = targets_from_data(data.get("targets"))
    resolved = await resolve_targets(app_config, refresh=True)
    if not resolved.targets:
        await callback.answer("Нет доступных площадок", show_alert=True)
        return

    selected = remap_selection(previous, _selected_from_data(data), resolved.targets)
    await state.update_data(
        targets=targets_to_data(resolved.targets),
        selected=sorted(selected),
    )
    await _edit_callback_message(
        callback,
        targets_text(len(selected), resolved),
        targets_keyboard(resolved.targets, selected),
    )
    await callback.answer("Список обновлён")


@router.callback_query(DraftAction.filter(F.action == "discard"))
async def discard_draft(callback: CallbackQuery, state: FSMContext) -> None:
    await _delete_current_draft_media(state)
    await state.clear()
    await _edit_callback_message(
        callback, "Черновик удалён. Отправьте /new, чтобы начать заново."
    )
    await callback.answer()


@router.callback_query(TargetAction.filter(F.action == "toggle"))
async def toggle_target(
    callback: CallbackQuery,
    callback_data: TargetAction,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    targets = targets_from_data(data.get("targets"))
    selected = _selected_from_data(data)
    try:
        selected = toggle_index(selected, callback_data.index, len(targets))
    except IndexError:
        await callback.answer(
            "Список площадок изменился. Откройте его заново.", show_alert=True
        )
        return
    await state.update_data(selected=sorted(selected))
    await _edit_callback_message(
        callback,
        targets_text(len(selected)),
        targets_keyboard(targets, selected),
    )
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "open"))
async def open_review(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = _selected_from_data(data)
    if not selected:
        await callback.answer("Выберите хотя бы одну площадку", show_alert=True)
        return
    targets = targets_from_data(data.get("targets"))
    draft = PostDraft.from_dict(data.get("draft"))
    await state.set_state(BotFlow.reviewing)
    await _edit_callback_message(
        callback,
        review_text(draft, targets, selected),
        review_keyboard(),
    )
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "draft"))
async def back_to_draft(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = PostDraft.from_dict(data.get("draft"))
    await state.set_state(BotFlow.collecting)
    await _edit_callback_message(callback, draft_text(draft), draft_keyboard())
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "targets"))
async def back_to_targets(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    targets = targets_from_data(data.get("targets"))
    selected = _selected_from_data(data)
    await state.set_state(BotFlow.selecting_targets)
    await _edit_callback_message(
        callback,
        targets_text(len(selected)),
        targets_keyboard(targets, selected),
    )
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "cancel"))
async def cancel_from_button(callback: CallbackQuery, state: FSMContext) -> None:
    await _delete_current_draft_media(state)
    await state.clear()
    await _edit_callback_message(
        callback, "Действие отменено, черновик удалён. Отправьте /new для нового поста."
    )
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "submit"))
async def submit_draft(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = _selected_from_data(data)
    if not selected:
        await callback.answer("Выберите хотя бы одну площадку", show_alert=True)
        return
    snapshot = targets_from_data(data.get("targets"))
    try:
        targets = tuple(snapshot[index] for index in sorted(selected))
    except IndexError:
        await callback.answer(
            "Список площадок изменился. Выберите цели заново.",
            show_alert=True,
        )
        return

    draft = PostDraft.from_dict(data.get("draft"))
    try:
        submission = save_submission(draft, targets)
    except SubmissionError as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to persist post submission")
        await callback.answer(
            "Не удалось сохранить пост. Попробуйте ещё раз.",
            show_alert=True,
        )
        return

    dispatch_result = await asyncio.to_thread(dispatch_jobs, submission.job_ids)

    names = "\n".join(f"• {escape(target.name)}" for target in targets)
    dispatch_text = (
        "Задания переданы Celery worker."
        if not dispatch_result.failed
        else "Часть заданий сохранена локально: Redis сейчас недоступен."
    )
    await state.clear()
    await _edit_callback_message(
        callback,
        "<b>Пост сохранён</b>\n\n"
        f"Номер поста: <code>{submission.post_id}</code>\n"
        f"Заданий создано: <b>{len(submission.job_ids)}</b>\n\n"
        f"Выбранные площадки:\n{names}\n\n"
        f"{dispatch_text}",
    )
    await callback.answer("Готово")


async def _show_or_refresh_draft(
    message: Message,
    state: FSMContext,
    draft: PostDraft,
) -> None:
    data = await state.get_data()
    prompt_chat_id = data.get("prompt_chat_id")
    prompt_message_id = data.get("prompt_message_id")
    if prompt_chat_id and prompt_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=prompt_chat_id,
                message_id=prompt_message_id,
                text=draft_text(draft),
                reply_markup=draft_keyboard(),
            )
            return
        except TelegramBadRequest:
            pass

    prompt = await message.answer(draft_text(draft), reply_markup=draft_keyboard())
    await state.update_data(
        prompt_chat_id=prompt.chat.id,
        prompt_message_id=prompt.message_id,
    )


async def _edit_callback_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: Any | None = None,
) -> None:
    if callback.message:
        await callback.message.edit_text(text, reply_markup=reply_markup)


def _selected_from_data(data: dict[str, Any]) -> set[int]:
    return {int(index) for index in data.get("selected", [])}


async def _delete_current_draft_media(state: FSMContext) -> None:
    data = await state.get_data()
    delete_draft_media(PostDraft.from_dict(data.get("draft")))
