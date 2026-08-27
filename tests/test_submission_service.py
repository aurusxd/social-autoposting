import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import PublishTarget
from app.core.drafts import DraftMedia, PostDraft
from app.database.models import Base, MediaFile, Post, PublishJob
from app.services.submission_service import SubmissionError, save_submission


def test_submission_persists_post_media_and_jobs() -> None:
    engine = create_engine("sqlite://")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    draft = PostDraft(
        caption="Текст публикации",
        media=(DraftMedia("media/photo.jpg", "photo"),),
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
    # Media now arrives from the browser, so there is no Telegram file id.
    assert media[0].tg_file_id is None
    assert media[0].file_path == "media/photo.jpg"
    assert [job.platform for job in jobs] == ["telegram", "instagram"]
    assert all(job.status == "pending" for job in jobs)
    assert result.job_ids == tuple(job.id for job in jobs)


def test_submission_rejects_unstored_media() -> None:
    engine = create_engine("sqlite://")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    draft = PostDraft(media=(DraftMedia("", "photo"),))
    targets = (PublishTarget("telegram", "-1001", "channel", "Основной"),)

    with pytest.raises(SubmissionError, match="медиафайлы"):
        save_submission(draft, targets, session_factory)


def test_submission_rejects_more_than_ten_files() -> None:
    engine = create_engine("sqlite://")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    draft = PostDraft(
        media=tuple(DraftMedia(f"media/{index}.jpg", "photo") for index in range(11))
    )
    targets = (PublishTarget("telegram", "-1001", "channel", "Основной"),)

    with pytest.raises(SubmissionError, match="не больше 10"):
        save_submission(draft, targets, session_factory)


def test_instagram_rejects_text_only_submission() -> None:
    draft = PostDraft(caption="Только текст")
    targets = (PublishTarget("instagram", "self", "feed", "Лента"),)

    with pytest.raises(SubmissionError, match="фото или видео"):
        save_submission(draft, targets)


def test_instagram_story_rejects_multiple_media() -> None:
    draft = PostDraft(
        media=(
            DraftMedia("media/first.jpg", "photo"),
            DraftMedia("media/second.jpg", "photo"),
        )
    )
    targets = (PublishTarget("instagram", "self", "story", "История"),)

    with pytest.raises(SubmissionError, match="ровно один"):
        save_submission(draft, targets)


def test_instagram_rejects_too_long_caption() -> None:
    draft = PostDraft(
        caption="a" * 2201,
        media=(DraftMedia("media/photo.jpg", "photo"),),
    )
    targets = (PublishTarget("instagram", "self", "feed", "Лента"),)

    with pytest.raises(SubmissionError, match="2200"):
        save_submission(draft, targets)


def test_tiktok_rejects_text_only_submission() -> None:
    draft = PostDraft(caption="Только текст")
    targets = (PublishTarget("tiktok", "self", "feed", "TikTok"),)

    with pytest.raises(SubmissionError, match="фото или видео"):
        save_submission(draft, targets)


def test_tiktok_rejects_mixed_media() -> None:
    draft = PostDraft(
        media=(
            DraftMedia("media/photo.jpg", "photo"),
            DraftMedia("media/video.mp4", "video"),
        )
    )
    targets = (PublishTarget("tiktok", "self", "feed", "TikTok"),)

    with pytest.raises(SubmissionError, match="смешивание"):
        save_submission(draft, targets)


def test_whatsapp_rejects_text_longer_than_api_limit() -> None:
    draft = PostDraft(caption="a" * 4097)
    targets = (PublishTarget("whatsapp", "120363123456789@g.us", "group", "Group"),)

    with pytest.raises(SubmissionError, match="4096"):
        save_submission(draft, targets)
