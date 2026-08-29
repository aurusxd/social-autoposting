from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (Index("idx_posts_scheduled_at", "scheduled_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    caption: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16),
        default="draft",
        server_default="draft",
    )
    # Naive UTC, like every other timestamp here. NULL means "publish at once".
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)

    media_files: Mapped[list[MediaFile]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
    )
    publish_jobs: Mapped[list[PublishJob]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
    )


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    file_path: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(16))
    tg_file_id: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(
        default=0,
        server_default="0",
    )

    post: Mapped[Post] = relationship(back_populates="media_files")


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        UniqueConstraint(
            "post_id",
            "platform",
            "target_key",
            "target_kind",
            name="uq_publish_jobs_post_target",
        ),
        Index("idx_publish_jobs_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    platform: Mapped[str] = mapped_column(String(32))
    target_key: Mapped[str] = mapped_column(String(255))
    target_kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default="pending",
    )
    attempts: Mapped[int] = mapped_column(
        default=0,
        server_default="0",
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    post: Mapped[Post] = relationship(back_populates="publish_jobs")
