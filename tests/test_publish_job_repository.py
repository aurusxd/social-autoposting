from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Base, Post, PublishJob
from app.database.repositories.publish_jobs_repo import PublishJobRepository

NOW = datetime(2026, 8, 29, 12, 0)


def _scheduled_post(
    session_factory: sessionmaker[Session],
    scheduled_at: datetime,
) -> tuple[int, int]:
    with session_factory.begin() as session:
        post = Post(caption="Отложенный", status="scheduled", scheduled_at=scheduled_at)
        session.add(post)
        session.flush()
        job = PublishJob(
            post_id=post.id,
            platform="telegram",
            target_key="-100123",
            target_kind="channel",
            status="scheduled",
        )
        session.add(job)
        session.flush()
        return post.id, job.id


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


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


def test_a_post_whose_time_has_come_is_queued() -> None:
    session_factory = _factory()
    post_id, job_id = _scheduled_post(session_factory, NOW - timedelta(minutes=1))

    with session_factory.begin() as session:
        released = PublishJobRepository(session).release_due(NOW)

    with session_factory() as session:
        post = session.get(Post, post_id)
        job = session.get(PublishJob, job_id)

    assert released == [job_id]
    assert job.status == "pending"
    assert post.status == "queued"
    # The planned time stays on the record so history can still show it.
    assert post.scheduled_at == NOW - timedelta(minutes=1)


def test_a_post_still_waiting_is_left_alone() -> None:
    session_factory = _factory()
    post_id, job_id = _scheduled_post(session_factory, NOW + timedelta(minutes=5))

    with session_factory.begin() as session:
        released = PublishJobRepository(session).release_due(NOW)

    with session_factory() as session:
        post = session.get(Post, post_id)
        job = session.get(PublishJob, job_id)

    assert released == []
    assert job.status == "scheduled"
    assert post.status == "scheduled"


def test_releasing_the_same_post_twice_queues_it_once() -> None:
    session_factory = _factory()
    _post_id, job_id = _scheduled_post(session_factory, NOW - timedelta(minutes=1))

    with session_factory.begin() as session:
        first = PublishJobRepository(session).release_due(NOW)
    with session_factory.begin() as session:
        second = PublishJobRepository(session).release_due(NOW)

    assert first == [job_id]
    assert second == []


def test_a_scheduled_job_cannot_be_claimed_before_it_is_released() -> None:
    session_factory = _factory()
    _post_id, job_id = _scheduled_post(session_factory, NOW + timedelta(hours=1))

    with session_factory.begin() as session:
        claimed = PublishJobRepository(session).claim_pending(job_id)

    assert claimed is None


def test_a_post_with_jobs_still_waiting_reads_as_scheduled() -> None:
    session_factory = _factory()
    post_id, _job_id = _scheduled_post(session_factory, NOW + timedelta(hours=1))

    with session_factory.begin() as session:
        status = PublishJobRepository(session).refresh_post_status(post_id)

    assert status == "scheduled"
