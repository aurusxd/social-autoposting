from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database.models import Post, PublishJob

MAX_ATTEMPTS = 3


class PublishJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def claim_pending(self, job_id: int) -> PublishJob | None:
        statement = (
            update(PublishJob)
            .where(
                PublishJob.id == job_id,
                PublishJob.status == "pending",
            )
            .values(
                status="in_progress",
                updated_at=func.now(),
            )
            .returning(PublishJob)
            .execution_options(synchronize_session=False)
        )

        return self.session.scalars(statement).one_or_none()

    def mark_done(self, job_id: int) -> bool:
        statement = (
            update(PublishJob)
            .where(
                PublishJob.id == job_id,
                PublishJob.status == "in_progress",
            )
            .values(
                status="done",
                last_error=None,
                updated_at=func.now(),
            )
            .returning(PublishJob.id)
            .execution_options(synchronize_session=False)
        )

        return self.session.scalar(statement) is not None

    def schedule_retry(self, job_id: int, error: str) -> int | None:
        statement = (
            update(PublishJob)
            .where(
                PublishJob.id == job_id,
                PublishJob.status == "in_progress",
                PublishJob.attempts < MAX_ATTEMPTS - 1,
            )
            .values(
                status="pending",
                attempts=PublishJob.attempts + 1,
                last_error=error,
                updated_at=func.now(),
            )
            .returning(PublishJob.attempts)
            .execution_options(synchronize_session=False)
        )

        return self.session.scalar(statement)

    def mark_failed(self, job_id: int, error: str) -> int | None:
        statement = (
            update(PublishJob)
            .where(
                PublishJob.id == job_id,
                PublishJob.status == "in_progress",
            )
            .values(
                status="failed",
                attempts=PublishJob.attempts + 1,
                last_error=error,
                updated_at=func.now(),
            )
            .returning(PublishJob.attempts)
            .execution_options(synchronize_session=False)
        )

        return self.session.scalar(statement)

    def pending_ids(self) -> list[int]:
        statement = (
            select(PublishJob.id)
            .where(PublishJob.status == "pending")
            .order_by(PublishJob.created_at, PublishJob.id)
        )
        return list(self.session.scalars(statement))

    def recover_stale(self, timeout_seconds: int) -> list[int]:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            seconds=timeout_seconds
        )
        statement = (
            update(PublishJob)
            .where(
                PublishJob.status == "in_progress",
                PublishJob.updated_at < cutoff,
            )
            .values(status="pending", updated_at=func.now())
            .returning(PublishJob.id)
            .execution_options(synchronize_session=False)
        )
        return list(self.session.scalars(statement))

    def refresh_post_status(self, post_id: int) -> str:
        statuses = set(
            self.session.scalars(
                select(PublishJob.status).where(PublishJob.post_id == post_id)
            )
        )
        if statuses & {"pending", "in_progress"}:
            status = "queued"
        elif "failed" in statuses:
            status = "failed"
        else:
            status = "done"

        self.session.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(status=status)
            .execution_options(synchronize_session=False)
        )
        return status
