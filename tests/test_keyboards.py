from app.bot.keyboards import targets_keyboard
from app.core.config import PublishTarget


def test_targets_keyboard_marks_selected_items_and_shows_count() -> None:
    targets = (
        PublishTarget("telegram", "-1001", "channel", "Новости"),
        PublishTarget("instagram", "self", "story", "История"),
    )

    markup = targets_keyboard(targets, {1})
    button_rows = markup.inline_keyboard

    assert button_rows[0][0].text.startswith("▫️")
    assert button_rows[1][0].text.startswith("✅")
    assert button_rows[2][0].text == "Продолжить · выбрано 1"


def test_target_callbacks_use_indexes_instead_of_external_ids() -> None:
    long_key = "target-key-" * 20
    target = PublishTarget("whatsapp", long_key, "group", "Длинная цель")

    markup = targets_keyboard((target,), set())
    callback_data = markup.inline_keyboard[0][0].callback_data

    assert callback_data is not None
    assert len(callback_data.encode()) <= 64
    assert long_key not in callback_data
