from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.scheduling import utc_now
from app.database.models import Post, PublishJob
from app.database.repositories.publish_jobs_repo import PublishJobRepository

TARGET = "telegram:channel:-1001"


def _moscow_input(ahead: timedelta) -> str:
    """The bare Moscow wall clock the panel puts in its date field."""
    return (utc_now() + ahead + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")


def _schedule(client: TestClient, ahead: timedelta) -> dict:
    response = client.post(
        "/api/posts",
        json={
            "caption": "Отложенный пост",
            "media": [],
            "targets": [TARGET],
            "scheduled_at": _moscow_input(ahead),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_scheduled_post_is_saved_but_not_dispatched(
    signed_in: TestClient,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    body = _schedule(signed_in, timedelta(minutes=5))

    with session_factory() as session:
        post = session.get(Post, body["post_id"])
        job = session.scalars(select(PublishJob)).one()

    assert post.status == "scheduled"
    assert post.scheduled_at is not None
    assert job.status == "scheduled"
    assert body["dispatched"] == 0
    assert body["failed"] == 0
    # Nothing reaches Celery until the time comes.
    assert dispatched == []


def test_the_panel_field_is_read_as_moscow_time(
    signed_in: TestClient,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    response = signed_in.post(
        "/api/posts",
        json={
            "caption": "Пост",
            "media": [],
            "targets": [TARGET],
            "scheduled_at": "2027-05-20T18:30",
        },
    )

    assert response.status_code == 201, response.text
    with session_factory() as session:
        post = session.get(Post, response.json()["post_id"])
    assert post.scheduled_at == datetime(2027, 5, 20, 15, 30)


def test_a_time_already_gone_is_refused(
    signed_in: TestClient,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    response = signed_in.post(
        "/api/posts",
        json={
            "caption": "Пост",
            "media": [],
            "targets": [TARGET],
            "scheduled_at": _moscow_input(timedelta(minutes=-5)),
        },
    )

    assert response.status_code == 400
    assert "в будущем" in response.json()["detail"]
    with session_factory() as session:
        assert session.scalars(select(Post)).all() == []
    assert dispatched == []


def test_a_post_without_a_time_publishes_at_once(
    signed_in: TestClient,
    dispatched: list[tuple[int, ...]],
) -> None:
    response = signed_in.post(
        "/api/posts",
        json={"caption": "Пост", "media": [], "targets": [TARGET]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["scheduled_at"] is None
    assert response.json()["dispatched"] == 1
    assert len(dispatched) == 1


def test_a_scheduled_post_can_be_moved_to_another_time(
    signed_in: TestClient,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    body = _schedule(signed_in, timedelta(minutes=5))

    response = signed_in.post(
        f"/api/posts/{body['post_id']}/schedule",
        json={"scheduled_at": "2027-05-20T18:30"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["job_ids"] == []
    with session_factory() as session:
        post = session.get(Post, body["post_id"])
        job = session.scalars(select(PublishJob)).one()
    assert post.scheduled_at == datetime(2027, 5, 20, 15, 30)
    assert post.status == "scheduled"
    assert job.status == "scheduled"
    assert dispatched == []


def test_a_scheduled_post_can_be_published_right_away(
    signed_in: TestClient,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    body = _schedule(signed_in, timedelta(hours=3))

    response = signed_in.post(
        f"/api/posts/{body['post_id']}/schedule",
        json={"scheduled_at": None},
    )

    assert response.status_code == 200, response.text
    with session_factory() as session:
        post = session.get(Post, body["post_id"])
        job = session.scalars(select(PublishJob)).one()
    assert post.status == "queued"
    assert post.scheduled_at is None
    assert job.status == "pending"
    assert response.json()["job_ids"] == [job.id]
    assert dispatched == [(job.id,)]


def test_moving_a_post_into_the_past_is_refused(
    signed_in: TestClient,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    body = _schedule(signed_in, timedelta(hours=3))

    response = signed_in.post(
        f"/api/posts/{body['post_id']}/schedule",
        json={"scheduled_at": _moscow_input(timedelta(minutes=-1))},
    )

    assert response.status_code == 400
    with session_factory() as session:
        assert session.get(Post, body["post_id"]).status == "scheduled"


def test_a_post_already_on_its_way_cannot_be_rescheduled(
    signed_in: TestClient,
    dispatched: list[tuple[int, ...]],
) -> None:
    created = signed_in.post(
        "/api/posts",
        json={"caption": "Пост", "media": [], "targets": [TARGET]},
    ).json()

    response = signed_in.post(
        f"/api/posts/{created['post_id']}/schedule",
        json={"scheduled_at": "2027-05-20T18:30"},
    )

    assert response.status_code == 409
    assert "не ждёт публикации" in response.json()["detail"]


def test_a_missing_post_cannot_be_rescheduled(signed_in: TestClient) -> None:
    response = signed_in.post(
        "/api/posts/404/schedule",
        json={"scheduled_at": "2027-05-20T18:30"},
    )

    assert response.status_code == 404


def test_a_scheduled_post_can_still_be_deleted(
    signed_in: TestClient,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    body = _schedule(signed_in, timedelta(hours=3))

    response = signed_in.post(f"/api/posts/{body['post_id']}/delete")

    assert response.status_code == 204
    with session_factory() as session:
        assert session.get(Post, body["post_id"]) is None


def test_the_history_shows_the_planned_time_in_moscow_time(
    signed_in: TestClient,
    dispatched: list[tuple[int, ...]],
) -> None:
    created = signed_in.post(
        "/api/posts",
        json={
            "caption": "Пост по расписанию",
            "media": [],
            "targets": [TARGET],
            "scheduled_at": "2027-05-20T18:30",
        },
    ).json()

    history = signed_in.get("/history?status=scheduled")
    details = signed_in.get(f"/posts/{created['post_id']}")

    assert history.status_code == 200
    assert "20.05.2027 18:30 МСК" in history.text
    assert details.status_code == 200
    assert "20.05.2027 18:30 МСК" in details.text
    assert 'value="2027-05-20T18:30"' in details.text


def test_the_worker_queues_a_post_once_its_time_has_come(
    signed_in: TestClient,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    body = _schedule(signed_in, timedelta(minutes=5))

    with session_factory.begin() as session:
        released = PublishJobRepository(session).release_due(
            utc_now() + timedelta(minutes=6)
        )

    with session_factory() as session:
        post = session.get(Post, body["post_id"])
        job = session.scalars(select(PublishJob)).one()

    assert released == [job.id]
    assert job.status == "pending"
    assert post.status == "queued"
