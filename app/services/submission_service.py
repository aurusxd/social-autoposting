from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import PublishTarget
from app.core.drafts import MEDIA_LIMIT, PostDraft
from app.core.scheduling import normalize_schedule
from app.database.database import SessionLocal
from app.database.models import MediaFile, Post, PublishJob


class SubmissionError(ValueError):
    """Raised when a draft cannot be persisted."""


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    post_id: int
    job_ids: tuple[int, ...]
    scheduled_at: datetime | None = None


def save_submission(
    draft: PostDraft,
    targets: tuple[PublishTarget, ...],
    session_factory: sessionmaker[Session] = SessionLocal,
    scheduled_at: datetime | None = None,
) -> SubmissionResult:
    if not draft.has_content:
        raise SubmissionError("Черновик пуст")
    if not targets:
        raise SubmissionError("Нужно выбрать хотя бы одну площадку")
    if len(draft.media) > MEDIA_LIMIT:
        raise SubmissionError(f"В одном посте не больше {MEDIA_LIMIT} файлов")
    if any(not media.file_path for media in draft.media):
        raise SubmissionError("Не все медиафайлы сохранены")
    if any(target.platform == "instagram" for target in targets):
        _validate_instagram_draft(draft, targets)
    if any(target.platform == "tiktok" for target in targets):
        _validate_tiktok_draft(draft)
    if any(target.platform == "whatsapp" for target in targets):
        _validate_whatsapp_draft(draft)

    planned_at = normalize_schedule(scheduled_at)
    job_status = "scheduled" if planned_at else "pending"

    with session_factory.begin() as session:
        post = Post(
            caption=draft.caption or None,
            status="scheduled" if planned_at else "queued",
            scheduled_at=planned_at,
        )
        session.add(post)
        session.flush()

        session.add_all(
            MediaFile(
                post_id=post.id,
                file_path=media.file_path,
                media_type=media.media_type,
                tg_file_id=None,
                position=position,
            )
            for position, media in enumerate(draft.media)
        )

        jobs = [
            PublishJob(
                post_id=post.id,
                platform=target.platform,
                target_key=target.key,
                target_kind=target.kind,
                status=job_status,
            )
            for target in targets
        ]
        session.add_all(jobs)
        session.flush()

        return SubmissionResult(
            post_id=post.id,
            job_ids=tuple(job.id for job in jobs),
            scheduled_at=planned_at,
        )


def _validate_instagram_draft(
    draft: PostDraft,
    targets: tuple[PublishTarget, ...],
) -> None:
    if not draft.media:
        raise SubmissionError("Для Instagram нужно добавить фото или видео")
    if len(draft.caption) > 2200:
        raise SubmissionError("Подпись Instagram превышает 2200 символов")
    if (
        any(
            target.platform == "instagram" and target.kind == "story"
            for target in targets
        )
        and len(draft.media) != 1
    ):
        raise SubmissionError("Для Instagram Story выберите ровно один файл")
    # A feed carousel also tops out at ten files, which is exactly MEDIA_LIMIT,
    # so the shared check above already covers it.


def _validate_tiktok_draft(draft: PostDraft) -> None:
    if not draft.media:
        raise SubmissionError("Для TikTok нужно добавить фото или видео")

    media_types = {media.media_type for media in draft.media}
    if len(media_types) > 1:
        raise SubmissionError("TikTok не поддерживает смешивание фото и видео")
    if "video" in media_types and len(draft.media) != 1:
        raise SubmissionError("Для TikTok выберите ровно одно видео")

    caption_limit = 2200 if "video" in media_types else 4000
    if len(draft.caption) > caption_limit:
        raise SubmissionError(f"Подпись TikTok превышает {caption_limit} символов")


def _validate_whatsapp_draft(draft: PostDraft) -> None:
    if len(draft.caption) > 4096:
        raise SubmissionError("Текст WhatsApp превышает 4096 символов")
