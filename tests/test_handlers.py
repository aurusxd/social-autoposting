from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import handlers
from app.bot.keyboards import TargetAction
from app.bot.models import PostDraft, targets_to_data
from app.core.config import AppConfig, PublishTarget, TelegramAPIConfig
from app.services.target_registry import ResolvedTargets

TELEGRAM = PublishTarget("telegram", "-1001", "channel", "Основной")
GROUP_A = PublishTarget("whatsapp", "1203630001@g.us", "group", "Клиенты")
GROUP_B = PublishTarget("whatsapp", "1203630002@g.us", "group", "Партнёры")
CHANNEL = PublishTarget("whatsapp", "1203630003@newsletter", "channel", "Канал")


class FakeMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def edit_text(self, text: str, reply_markup: Any = None) -> None:
        self.texts.append(text)


class FakeCallback:
    def __init__(self) -> None:
        self.message = FakeMessage()
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def _state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=1),
    )


def _config() -> AppConfig:
    return AppConfig(
        bot_token="token",
        owner_id=1,
        telegram_api=TelegramAPIConfig(
            base_url="https://api.telegram.org",
            local=False,
            server_files_path=Path("/tmp"),
            client_files_path=Path("/tmp"),
        ),
        # Deliberately empty: WhatsApp chats never come from the config any more.
        targets=(),
        whatsapp=None,
        instagram=None,
        tiktok=None,
    )


def _draft_data() -> dict[str, Any]:
    return {"draft": PostDraft(caption="Привет").to_dict()}


def _stub_resolver(
    monkeypatch: pytest.MonkeyPatch,
    *batches: tuple[PublishTarget, ...],
) -> None:
    """Return each batch in turn, so a test can change the chat list mid-flow."""
    queue = list(batches)

    async def fake_resolve(_config: AppConfig, **_: Any) -> ResolvedTargets:
        return ResolvedTargets(queue.pop(0) if len(queue) > 1 else queue[0])

    monkeypatch.setattr(handlers, "resolve_targets", fake_resolve)


def test_open_targets_stores_the_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolver(monkeypatch, (TELEGRAM, GROUP_A))
    state = _state()
    callback = FakeCallback()

    async def run() -> dict[str, Any]:
        await state.update_data(**_draft_data())
        await handlers.open_targets(callback, state, _config())
        return await state.get_data()

    data = asyncio.run(run())

    assert data["targets"] == targets_to_data((TELEGRAM, GROUP_A))


def test_open_targets_refuses_an_empty_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolver(monkeypatch, (TELEGRAM,))
    state = _state()
    callback = FakeCallback()

    asyncio.run(handlers.open_targets(callback, state, _config()))

    assert callback.answers == [("Сначала добавьте контент", True)]


def test_toggle_uses_the_snapshot_not_the_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolver(monkeypatch, (TELEGRAM, GROUP_A))
    state = _state()

    async def run() -> dict[str, Any]:
        await state.update_data(**_draft_data())
        await handlers.open_targets(FakeCallback(), state, _config())
        await handlers.toggle_target(
            FakeCallback(),
            TargetAction(action="toggle", index=1),
            state,
        )
        return await state.get_data()

    data = asyncio.run(run())

    # config.targets is empty, so index 1 can only come from the snapshot.
    assert data["selected"] == [1]


def test_selection_follows_the_chat_when_the_list_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A new channel appears at the front on the second open, shifting indexes.
    _stub_resolver(
        monkeypatch,
        (TELEGRAM, GROUP_A, GROUP_B),
        (TELEGRAM, CHANNEL, GROUP_A, GROUP_B),
    )
    state = _state()

    async def run() -> dict[str, Any]:
        await state.update_data(**_draft_data())
        await handlers.open_targets(FakeCallback(), state, _config())
        await handlers.toggle_target(
            FakeCallback(),
            TargetAction(action="toggle", index=2),  # GROUP_B
            state,
        )
        await handlers.open_targets(FakeCallback(), state, _config())
        return await state.get_data()

    data = asyncio.run(run())

    chosen = [
        target["name"]
        for index, target in enumerate(data["targets"])
        if index in set(data["selected"])
    ]
    assert chosen == ["Партнёры"]


def test_refresh_remaps_the_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolver(
        monkeypatch,
        (TELEGRAM, GROUP_A, GROUP_B),
        (TELEGRAM, CHANNEL, GROUP_B),
    )
    state = _state()
    callback = FakeCallback()

    async def run() -> dict[str, Any]:
        await state.update_data(**_draft_data())
        await handlers.open_targets(FakeCallback(), state, _config())
        await handlers.toggle_target(
            FakeCallback(),
            TargetAction(action="toggle", index=1),  # GROUP_A, which disappears
            state,
        )
        await handlers.toggle_target(
            FakeCallback(),
            TargetAction(action="toggle", index=2),  # GROUP_B, which survives
            state,
        )
        await handlers.refresh_targets(callback, state, _config())
        return await state.get_data()

    data = asyncio.run(run())

    chosen = [
        target["name"]
        for index, target in enumerate(data["targets"])
        if index in set(data["selected"])
    ]
    # GROUP_A is gone, GROUP_B moved from index 2 to index 2 of a shorter list.
    assert chosen == ["Партнёры"]
    assert callback.answers == [("Список обновлён", False)]


def test_submit_publishes_the_snapshot_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolver(monkeypatch, (TELEGRAM, GROUP_A, CHANNEL))
    captured: dict[str, Any] = {}

    def fake_save(draft: PostDraft, targets: tuple[PublishTarget, ...]) -> Any:
        captured["targets"] = targets

        class Result:
            post_id = 7
            job_ids = (1, 2)

        return Result()

    def fake_dispatch(job_ids: tuple[int, ...]) -> Any:
        class Result:
            failed: tuple[int, ...] = ()

        return Result()

    monkeypatch.setattr(handlers, "save_submission", fake_save)
    monkeypatch.setattr(handlers, "dispatch_jobs", fake_dispatch)
    state = _state()

    async def run() -> None:
        await state.update_data(**_draft_data())
        await handlers.open_targets(FakeCallback(), state, _config())
        await handlers.toggle_target(
            FakeCallback(),
            TargetAction(action="toggle", index=2),  # CHANNEL
            state,
        )
        await handlers.submit_draft(FakeCallback(), state)

    asyncio.run(run())

    # The config carries no WhatsApp targets at all, so this can only have come
    # from the snapshot stored when the list was opened.
    assert captured["targets"] == (CHANNEL,)


def test_submit_without_a_selection_is_refused() -> None:
    state = _state()
    callback = FakeCallback()

    asyncio.run(handlers.submit_draft(callback, state))

    assert callback.answers == [("Выберите хотя бы одну площадку", True)]
