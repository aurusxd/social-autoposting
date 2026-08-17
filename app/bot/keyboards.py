from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.config import PublishTarget


class DraftAction(CallbackData, prefix="draft"):
    action: str


class TargetAction(CallbackData, prefix="target"):
    action: str
    index: int


class ReviewAction(CallbackData, prefix="review"):
    action: str


def draft_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎯 Выбрать площадки",
            callback_data=DraftAction(action="targets").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Сбросить черновик",
            callback_data=DraftAction(action="discard").pack(),
        )
    )
    return builder.as_markup()


def targets_keyboard(
    targets: tuple[PublishTarget, ...], selected: set[int]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, target in enumerate(targets):
        marker = "✅" if index in selected else "▫️"
        builder.row(
            InlineKeyboardButton(
                text=f"{marker} {_target_label(target)}",
                callback_data=TargetAction(action="toggle", index=index).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=f"Продолжить · выбрано {len(selected)}",
            callback_data=ReviewAction(action="open").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="← К черновику",
            callback_data=ReviewAction(action="draft").pack(),
        ),
        InlineKeyboardButton(
            text="Отмена",
            callback_data=ReviewAction(action="cancel").pack(),
        ),
    )
    return builder.as_markup()


def review_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=ReviewAction(action="submit").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="← Изменить площадки",
            callback_data=ReviewAction(action="targets").pack(),
        ),
        InlineKeyboardButton(
            text="Отмена",
            callback_data=ReviewAction(action="cancel").pack(),
        ),
    )
    return builder.as_markup()


def _target_label(target: PublishTarget) -> str:
    platform_icons = {
        "telegram": "✈️",
        "whatsapp": "💬",
        "instagram": "📸",
        "tiktok": "🎵",
    }
    return f"{platform_icons[target.platform]} {target.name}"
