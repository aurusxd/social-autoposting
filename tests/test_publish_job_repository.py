from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Post, PublishJob
from app.database.repositories.publish_jobs_repo import PublishJobRepository


def test_pending_job_can_be_claimed_only_once() -> None:
    engine = create_engine("sqlite://")
    session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)

    with session_factory.begin() as session:
        post = Post(caption="Тестовый пост")
        session.add(post)
        session.flush()

        job = PublishJob(
            post_id=post.id,
            platform="telegram",
            target_key="-100123",
            target_kind="channel",
        )
        session.add(job)
        session.flush()
        job_id = job.id

    with session_factory.begin() as session:
        repository = PublishJobRepository(session)

        first_claim = repository.claim_pending(job_id)
        second_claim = repository.claim_pending(job_id)

        assert first_claim is not None
        assert first_claim.status == "in_progress"
        assert second_claim is None


def test_retryable_job_fails_after_three_attempts() -> None:
    engine = create_engine("sqlite://")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    with session_factory.begin() as session:
        post = Post(caption="Тест ретраев")
        session.add(post)
        session.flush()
        job = PublishJob(
            post_id=post.id,
            platform="telegram",
            target_key="-100123",
            target_kind="channel",
        )
        session.add(job)
        session.flush()
        job_id = job.id

    with session_factory.begin() as session:
        repository = PublishJobRepository(session)

        assert repository.claim_pending(job_id) is not None
        assert repository.schedule_retry(job_id, "Ошибка 1") == 1
        assert repository.claim_pending(job_id) is not None
        assert repository.schedule_retry(job_id, "Ошибка 2") == 2
        assert repository.claim_pending(job_id) is not None
        assert repository.schedule_retry(job_id, "Ошибка 3") is None
        assert repository.mark_failed(job_id, "Ошибка 3") == 3

        session.expire_all()
        stored_job = session.get(PublishJob, job_id)
        assert stored_job is not None
        assert stored_job.status == "failed"
        assert stored_job.attempts == 3
        assert stored_job.last_error == "Ошибка 3"


def test_claimed_job_can_be_marked_done() -> None:
    engine = create_engine("sqlite://")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    with session_factory.begin() as session:
        post = Post(caption="Успешный пост")
        session.add(post)
        session.flush()
        job = PublishJob(
            post_id=post.id,
            platform="telegram",
            target_key="-100123",
            target_kind="channel",
        )
        session.add(job)
        session.flush()
        job_id = job.id

    with session_factory.begin() as session:
        repository = PublishJobRepository(session)
        assert repository.claim_pending(job_id) is not None
        assert repository.mark_done(job_id)

        session.expire_all()
        stored_job = session.get(PublishJob, job_id)
        assert stored_job is not None
        assert stored_job.status == "done"
        assert stored_job.last_error is None
