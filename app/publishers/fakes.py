from __future__ import annotations

from dataclasses import dataclass, field

from app.publishers.base import Post, PublishResult, PublishTarget


@dataclass(slots=True)
class FakePublisher:
    platform: str
    result: PublishResult = field(default_factory=lambda: PublishResult(success=True))
    calls: list[tuple[Post, PublishTarget]] = field(default_factory=list)

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        self.calls.append((post, target))
        return self.result
