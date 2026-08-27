from __future__ import annotations

import pytest

from app.core.config import PublishTarget
from app.web.presenters import (
    UnknownTargetError,
    group_targets,
    select_targets,
    target_id,
)

TELEGRAM = PublishTarget("telegram", "-1001", "channel", "Основной")
GROUP = PublishTarget("whatsapp", "1203630001@g.us", "group", "Клиенты")
CHANNEL = PublishTarget("whatsapp", "1203630003@newsletter", "channel", "Канал")
STORY = PublishTarget("instagram", "self", "story", "История")


def test_identity_ignores_the_display_name() -> None:
    renamed = PublishTarget("whatsapp", GROUP.key, "group", "Клиенты 2026")

    assert target_id(renamed) == target_id(GROUP)


def test_the_same_key_in_another_kind_is_a_different_target() -> None:
    assert target_id(GROUP) != target_id(
        PublishTarget("whatsapp", GROUP.key, "channel", "Клиенты")
    )


def test_selection_survives_a_reordered_and_renamed_list() -> None:
    chosen = [target_id(GROUP)]
    # A new channel appears first and the group is renamed upstream.
    renamed = PublishTarget("whatsapp", GROUP.key, "group", "Новое")
    current = (TELEGRAM, CHANNEL, renamed)

    selected = select_targets(current, chosen)

    assert [target.key for target in selected] == [GROUP.key]


def test_a_vanished_target_is_refused_instead_of_silently_dropped() -> None:
    with pytest.raises(UnknownTargetError):
        select_targets((TELEGRAM,), [target_id(GROUP)])


def test_a_repeated_identifier_produces_one_target() -> None:
    selected = select_targets((TELEGRAM,), [target_id(TELEGRAM), target_id(TELEGRAM)])

    assert len(selected) == 1


def test_targets_are_grouped_in_platform_order() -> None:
    groups = group_targets((STORY, GROUP, TELEGRAM))

    assert [group["platform"] for group in groups] == [
        "telegram",
        "whatsapp",
        "instagram",
    ]
    whatsapp = groups[1]
    assert whatsapp["label"] == "WhatsApp"
    assert whatsapp["targets"][0]["kind_label"] == "группа"
