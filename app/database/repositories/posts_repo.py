from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.database.models import MediaFile, Post, PublishJob

RETRYABLE_STATUSES = ("failed",)


class PostRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_posts(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[Post]:
        statement = (
            select(Post)
            .options(
                selectinload(Post.media_files),
                selectinload(Post.publish_jobs),
            )
            .order_by(Post.created_at.desc(), Post.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            statement = statement.where(Post.status == status)
        return list(self.session.scalars(statement))

    def count_posts(self, status: str | None = None) -> int:
        statement = select(func.count()).select_from(Post)
        if status:
            statement = statement.where(Post.status == status)
        return self.session.scalar(statement) or 0

    def get_post(self, post_id: int) -> Post | None:
        statement = (
            select(Post)
            .options(
                selectinload(Post.media_files),
                selectinload(Post.publish_jobs),
            )
            .where(Post.id == post_id)
        )
        return self.session.scalars(statement).one_or_none()

    def requeue_failed(self, post_id: int) -> list[int]:
        """Reset failed jobs of a post so a worker can pick them up again."""
        statement = (
            update(PublishJob)
            .where(
                PublishJob.post_id == post_id,
                PublishJob.status.in_(RETRYABLE_STATUSES),
            )
            .values(status="pending", attempts=0, updated_at=func.now())
            .returning(PublishJob.id)
            .execution_options(synchronize_session=False)
        )
        job_ids = list(self.session.scalars(statement))
        if job_ids:
            self.session.execute(
                update(Post)
                .where(Post.id == post_id)
                .values(status="queued")
                .execution_options(synchronize_session=False)
            )
        return job_ids

    def media_paths(self, post_id: int) -> list[str]:
        statement = select(MediaFile.file_path).where(MediaFile.post_id == post_id)
        return list(self.session.scalars(statement))

    def delete_post(self, post_id: int) -> bool:
        """Remove a post that is not being published right now."""
        post = self.session.get(Post, post_id)
        if post is None:
            return False
        active = self.session.scalar(
            select(func.count())
            .select_from(PublishJob)
            .where(
                PublishJob.post_id == post_id,
                PublishJob.status.in_(("pending", "in_progress")),
            )
        )
        if active:
            return False
        self.session.delete(post)
        return True
