from __future__ import annotations

from app.core.config import PublishTarget

PLATFORM_LABELS = {
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

KIND_LABELS = {
    "channel": "канал",
    "group": "группа",
    "feed": "лента",
    "story": "история",
}

STATUS_LABELS = {
    "draft": "черновик",
    "scheduled": "запланировано",
    "queued": "в очереди",
    "pending": "ожидает",
    "in_progress": "публикуется",
    "done": "опубликовано",
    "failed": "ошибка",
}

PLATFORM_ORDER = ("telegram", "whatsapp", "instagram", "tiktok")


class UnknownTargetError(ValueError):
    """Raised when the browser sends a target the server does not offer."""


def target_id(target: PublishTarget) -> str:
    return f"{target.platform}:{target.kind}:{target.key}"


def select_targets(
    available: tuple[PublishTarget, ...],
    identifiers: list[str],
) -> tuple[PublishTarget, ...]:
    """Map ids from the browser back onto targets the server just resolved.

    Anything the server does not currently offer is rejected instead of being
    trusted, so a stale or edited page cannot publish somewhere unexpected.
    """
    by_id = {target_id(target): target for target in available}
    chosen: dict[str, PublishTarget] = {}
    for identifier in identifiers:
        target = by_id.get(identifier)
        if target is None:
            raise UnknownTargetError(
                "Список площадок изменился. Обновите его и выберите заново."
            )
        chosen[identifier] = target
    return tuple(chosen.values())


def group_targets(
    targets: tuple[PublishTarget, ...],
) -> list[dict[str, object]]:
    """Order targets by platform so the page can render one block per platform."""
    grouped: dict[str, list[PublishTarget]] = {}
    for target in targets:
        grouped.setdefault(target.platform, []).append(target)

    def order(platform: str) -> int:
        try:
            return PLATFORM_ORDER.index(platform)
        except ValueError:
            return len(PLATFORM_ORDER)

    return [
        {
            "platform": platform,
            "label": PLATFORM_LABELS.get(platform, platform.title()),
            "targets": [
                {
                    "id": target_id(target),
                    "name": target.name,
                    "kind": target.kind,
                    "kind_label": KIND_LABELS.get(target.kind, target.kind),
                }
                for target in grouped[platform]
            ],
        }
        for platform in sorted(grouped, key=order)
    ]


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform.title())


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.0f} КБ"
    return f"{size_bytes / 1024**2:.1f} МБ"
