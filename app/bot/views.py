from __future__ import annotations

from html import escape

from app.bot.models import PostDraft
from app.core.config import PublishTarget


def welcome_text() -> str:
    return (
        "<b>Новый пост</b>\n\n"
        "Пришлите текст, фото или видео. Можно отправить несколько файлов "
        "по очереди, а затем выбрать площадки публикации."
    )


def draft_text(draft: PostDraft) -> str:
    parts = ["<b>Черновик сохранён</b>"]
    if draft.caption:
        preview = draft.caption[:240]
        suffix = "…" if len(draft.caption) > 240 else ""
        parts.append(f"<b>Текст:</b> {escape(preview)}{suffix}")
    if draft.media:
        photo_count = sum(item.media_type == "photo" for item in draft.media)
        video_count = sum(item.media_type == "video" for item in draft.media)
        media_parts = []
        if photo_count:
            media_parts.append(f"фото: {photo_count}")
        if video_count:
            media_parts.append(f"видео: {video_count}")
        parts.append(f"<b>Медиа:</b> {', '.join(media_parts)}")
    parts.append("Можно прислать ещё контент или перейти к выбору площадок.")
    return "\n\n".join(parts)


def targets_text(selected_count: int) -> str:
    return (
        "<b>Куда опубликовать?</b>\n\n"
        "Нажимайте на площадки, чтобы включать или выключать их. "
        f"Сейчас выбрано: <b>{selected_count}</b>."
    )


def review_text(
    draft: PostDraft,
    targets: tuple[PublishTarget, ...],
    selected: set[int],
) -> str:
    selected_targets = [targets[index] for index in sorted(selected)]
    target_lines = "\n".join(f"• {escape(target.name)}" for target in selected_targets)
    content_parts = []
    if draft.caption:
        content_parts.append(f"текст, {len(draft.caption)} симв.")
    if draft.media:
        content_parts.append(f"медиа, {len(draft.media)} шт.")
    return (
        "<b>Проверьте пост</b>\n\n"
        f"<b>Контент:</b> {', '.join(content_parts)}\n"
        f"<b>Площадки:</b>\n{target_lines}\n\n"
        "После подтверждения черновик будет готов к передаче в очередь."
    )
