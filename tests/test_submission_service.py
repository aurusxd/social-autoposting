from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.bot.models import DraftMedia, PostDraft
from app.core.config import PublishTarget
from app.database.models import Base, MediaFile, Post, PublishJob
from app.services.submission_service import SubmissionError, save_submission


def test_submission_persists_post_media_and_jobs() -> None:
    engine = create_engine("sqlite://")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    draft = PostDraft(
        caption="Текст публикации",
        media=(DraftMedia("tg-photo", "photo", "media/photo.jpg"),),
    )
    targets = (
        PublishTarget("telegram", "-1001", "channel", "Основной"),
        PublishTarget("instagram", "self", "story", "История"),
    )

    result = save_submission(draft, targets, session_factory)

    with session_factory() as session:
        post = session.get(Post, result.post_id)
        media = session.scalars(select(MediaFile)).all()
        jobs = session.scalars(select(PublishJob).order_by(PublishJob.id)).all()

    assert post is not None
    assert post.caption == "Текст публикации"
    assert post.status == "queued"
    assert len(media) == 1
    assert media[0].tg_file_id == "tg-photo"
    assert media[0].file_path == "media/photo.jpg"
    assert [job.platform for job in jobs] == ["telegram", "instagram"]
    assert all(job.status == "pending" for job in jobs)
    assert result.job_ids == tuple(job.id for job in jobs)


def test_submission_rejects_unstored_media() -> None:
    engine = create_engine("sqlite://")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    draft = PostDraft(media=(DraftMedia("tg-photo", "photo"),))
    targets = (PublishTarget("telegram", "-1001", "channel", "Основной"),)

    try:
        save_submission(draft, targets, session_factory)
    except SubmissionError as error:
        assert "медиафайлы" in str(error)
    else:
        raise AssertionError("SubmissionError was not raised")
