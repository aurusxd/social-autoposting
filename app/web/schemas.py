from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.drafts import MEDIA_LIMIT


class MediaOut(BaseModel):
    token: str
    file_name: str
    media_type: str
    size_bytes: int
    size_label: str
    preview_url: str


class MediaTokenIn(BaseModel):
    token: str


class TargetOut(BaseModel):
    id: str
    name: str
    kind: str
    kind_label: str


class TargetGroupOut(BaseModel):
    platform: str
    label: str
    targets: list[TargetOut]


class TargetsOut(BaseModel):
    groups: list[TargetGroupOut]
    whatsapp_failed: bool = False
    truncated: bool = False


class PostCreateIn(BaseModel):
    caption: str = ""
    media: list[str] = Field(default_factory=list, max_length=MEDIA_LIMIT)
    targets: list[str] = Field(min_length=1)
    # Absent or null publishes at once; the panel sends an offset-aware moment.
    scheduled_at: datetime | None = None


class PostCreatedOut(BaseModel):
    post_id: int
    job_count: int
    dispatched: int
    failed: int
    scheduled_at: datetime | None = None


class ScheduleIn(BaseModel):
    """A new time for a waiting post, or null to publish it right away."""

    scheduled_at: datetime | None = None


class ScheduleOut(BaseModel):
    post_id: int
    scheduled_at: datetime | None
    job_ids: list[int]


class JobIdsOut(BaseModel):
    job_ids: list[int]


class ErrorOut(BaseModel):
    detail: str
