from __future__ import annotations

from app.bot.models import (
    remap_selection,
    target_identity,
    targets_from_data,
    targets_to_data,
)
from app.core.config import PublishTarget

TELEGRAM = PublishTarget("telegram", "-1001", "channel", "Основной")
GROUP_A = PublishTarget("whatsapp", "1203630001@g.us", "group", "Клиенты")
GROUP_B = PublishTarget("whatsapp", "1203630002@g.us", "group", "Партнёры")
CHANNEL = PublishTarget("whatsapp", "1203630003@newsletter", "channel", "Канал")


def test_snapshot_survives_a_serialisation_round_trip() -> None:
    targets = (TELEGRAM, GROUP_A, CHANNEL)

    assert targets_from_data(targets_to_data(targets)) == targets


def test_empty_snapshot_reads_back_as_empty() -> None:
    assert targets_from_data(None) == ()
    assert targets_from_data([]) == ()


def test_identity_ignores_the_display_name() -> None:
    renamed = PublishTarget("whatsapp", GROUP_A.key, "group", "Клиенты 2026")

    assert target_identity(renamed) == target_identity(GROUP_A)


def test_selection_follows_the_target_when_the_order_changes() -> None:
    previous = (TELEGRAM, GROUP_A, GROUP_B)
    # A new group appears first, pushing everything down by one.
    current = (TELEGRAM, CHANNEL, GROUP_A, GROUP_B)

    assert remap_selection(previous, {1}, current) == {2}


def test_selection_survives_a_rename() -> None:
    previous = (GROUP_A,)
    current = (PublishTarget("whatsapp", GROUP_A.key, "group", "Новое имя"),)

    assert remap_selection(previous, {0}, current) == {0}


def test_vanished_target_drops_out_of_the_selection() -> None:
    previous = (TELEGRAM, GROUP_A)
    current = (TELEGRAM,)

    assert remap_selection(previous, {0, 1}, current) == {0}


def test_out_of_range_indexes_are_ignored() -> None:
    previous = (TELEGRAM,)

    assert remap_selection(previous, {0, 5}, (TELEGRAM,)) == {0}


def test_same_key_in_a_different_kind_is_not_the_same_target() -> None:
    previous = (GROUP_A,)
    current = (PublishTarget("whatsapp", GROUP_A.key, "channel", "Клиенты"),)

    assert remap_selection(previous, {0}, current) == set()
