from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.scheduling import utc_now
from app.database.models import Base, Post, PublishJob
from app.publishers import PublishResult
from app.worker import tasks


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def _create_job(factory: sessionmaker[Session]) -> tuple[int, int]:
    with factory.begin() as session:
        post = Post(caption="Worker test", status="queued")
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
        return post.id, job.id


def _stored_statuses(
    factory: sessionmaker[Session],
    post_id: int,
    job_id: int,
) -> tuple[str, str, int]:
    with factory() as session:
        post = session.get(Post, post_id)
        job = session.get(PublishJob, job_id)
        assert post is not None
        assert job is not None
        return post.status, job.status, job.attempts


def test_successful_job_is_published_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _session_factory()
    post_id, job_id = _create_job(factory)
    calls = []

    def successful_publisher(job: tasks.ClaimedJob) -> PublishResult:
        calls.append(job)
        return PublishResult(success=True, external_id="message-1")

    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "run_publisher", successful_publisher)

    first = tasks.process_publish_job(job_id)
    second = tasks.process_publish_job(job_id)

    assert first.state == "done"
    assert second.state == "ignored"
    assert len(calls) == 1
    assert _stored_statuses(factory, post_id, job_id) == ("done", "done", 0)


def test_retryable_job_fails_on_third_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _session_factory()
    post_id, job_id = _create_job(factory)

    def temporary_failure(_: tasks.ClaimedJob) -> PublishResult:
        return PublishResult(success=False, retryable=True, error="timeout")

    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "run_publisher", temporary_failure)

    first = tasks.process_publish_job(job_id)
    second = tasks.process_publish_job(job_id)
    third = tasks.process_publish_job(job_id)

    assert (first.state, first.attempt) == ("retry", 1)
    assert first.retry_after is None
    assert (second.state, second.attempt) == ("retry", 2)
    assert third.state == "failed"
    assert _stored_statuses(factory, post_id, job_id) == ("failed", "failed", 3)


def test_non_retryable_error_fails_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _session_factory()
    post_id, job_id = _create_job(factory)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(
        tasks,
        "run_publisher",
        lambda _: PublishResult(
            success=False,
            retryable=False,
            error="invalid credentials",
        ),
    )

    outcome = tasks.process_publish_job(job_id)

    assert outcome.state == "failed"
    assert _stored_statuses(factory, post_id, job_id) == ("failed", "failed", 1)


def test_celery_registers_worker_tasks() -> None:
    assert "worker.healthcheck" in tasks.celery.tasks
    assert "worker.publish_job" in tasks.celery.tasks
    assert "worker.release_due_posts" in tasks.celery.tasks


def test_beat_is_set_to_look_for_due_posts() -> None:
    entry = tasks.celery.conf.beat_schedule["release-due-posts"]

    assert entry["task"] == "worker.release_due_posts"
    assert entry["schedule"] > 0


def _create_scheduled_job(
    factory: sessionmaker[Session],
    ahead: timedelta,
) -> tuple[int, int]:
    with factory.begin() as session:
        post = Post(
            caption="Отложенный",
            status="scheduled",
            scheduled_at=utc_now() + ahead,
        )
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


def test_beat_hands_a_due_post_to_the_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _session_factory()
    post_id, job_id = _create_scheduled_job(factory, timedelta(minutes=-1))
    waiting_post_id, waiting_job_id = _create_scheduled_job(factory, timedelta(hours=1))
    queued: list[int] = []
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_enqueue", queued.append)

    released = tasks.release_due_posts()

    assert released == 1
    assert queued == [job_id]
    assert _stored_statuses(factory, post_id, job_id) == ("queued", "pending", 0)
    assert _stored_statuses(factory, waiting_post_id, waiting_job_id) == (
        "scheduled",
        "scheduled",
        0,
    )


def test_beat_leaves_nothing_to_do_when_no_post_is_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _session_factory()
    _create_scheduled_job(factory, timedelta(hours=1))
    queued: list[int] = []
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_enqueue", queued.append)

    assert tasks.release_due_posts() == 0
    assert queued == []
