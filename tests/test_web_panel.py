from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import MediaFile, Post, PublishJob

PIXEL = b"\xff\xd8\xff\xe0fake-jpeg-body"


def _upload(client: TestClient, name: str = "cat.jpg", body: bytes = PIXEL) -> dict:
    response = client.post("/api/media", files={"file": (name, body, "image/jpeg")})
    assert response.status_code == 201, response.text
    return response.json()


def test_targets_are_grouped_for_the_page(signed_in: TestClient) -> None:
    payload = signed_in.get("/api/targets").json()

    assert [group["platform"] for group in payload["groups"]] == [
        "telegram",
        "instagram",
    ]
    assert payload["groups"][0]["targets"][0]["id"] == "telegram:channel:-1001"


def test_an_upload_is_stored_and_can_be_previewed(
    signed_in: TestClient,
    media_root: Path,
) -> None:
    uploaded = _upload(signed_in)

    stored = list(media_root.iterdir())
    assert len(stored) == 1
    assert stored[0].read_bytes() == PIXEL
    assert uploaded["media_type"] == "photo"
    assert uploaded["file_name"] == "cat.jpg"

    preview = signed_in.get(uploaded["preview_url"])
    assert preview.status_code == 200
    assert preview.content == PIXEL


def test_an_unsupported_upload_is_refused(
    signed_in: TestClient,
    media_root: Path,
) -> None:
    response = signed_in.post(
        "/api/media",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400
    assert "не поддерживается" in response.json()["detail"]
    assert list(media_root.iterdir()) == []


def test_an_upload_can_be_dropped_again(
    signed_in: TestClient,
    media_root: Path,
) -> None:
    uploaded = _upload(signed_in)

    response = signed_in.post("/api/media/delete", json={"token": uploaded["token"]})

    assert response.status_code == 204
    assert list(media_root.iterdir()) == []


def test_publishing_stores_the_post_and_queues_every_target(
    signed_in: TestClient,
    media_root: Path,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    uploaded = _upload(signed_in)

    response = signed_in.post(
        "/api/posts",
        json={
            "caption": "  Привет из панели  ",
            "media": [uploaded["token"]],
            "targets": ["telegram:channel:-1001", "instagram:feed:self"],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_count"] == 2
    assert body["dispatched"] == 2
    assert body["failed"] == 0

    with session_factory() as session:
        post = session.get(Post, body["post_id"])
        media = session.scalars(select(MediaFile)).all()
        jobs = session.scalars(select(PublishJob)).all()

    assert post is not None
    assert post.caption == "Привет из панели"
    assert post.status == "queued"
    assert [item.file_path for item in media] == [
        Path("media").joinpath(next(iter(media_root.iterdir())).name).as_posix()
    ]
    assert {job.platform for job in jobs} == {"telegram", "instagram"}
    assert dispatched == [tuple(job.id for job in jobs)]


def test_media_order_follows_the_panel(
    signed_in: TestClient,
    media_root: Path,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    first = _upload(signed_in, "one.jpg")
    second = _upload(signed_in, "two.jpg")

    response = signed_in.post(
        "/api/posts",
        json={
            "caption": "",
            "media": [second["token"], first["token"]],
            "targets": ["telegram:channel:-1001"],
        },
    )
    assert response.status_code == 201, response.text

    with session_factory() as session:
        media = session.scalars(select(MediaFile).order_by(MediaFile.position)).all()

    assert [item.position for item in media] == [0, 1]
    assert media[0].file_path != media[1].file_path


def test_a_target_the_server_does_not_offer_is_refused(
    signed_in: TestClient,
    dispatched: list[tuple[int, ...]],
) -> None:
    response = signed_in.post(
        "/api/posts",
        json={
            "caption": "Текст",
            "media": [],
            "targets": ["whatsapp:group:1203630001@g.us"],
        },
    )

    assert response.status_code == 409
    assert "Список площадок изменился" in response.json()["detail"]
    assert dispatched == []


def test_a_forged_media_token_is_refused(
    signed_in: TestClient,
    dispatched: list[tuple[int, ...]],
) -> None:
    response = signed_in.post(
        "/api/posts",
        json={
            "caption": "Текст",
            "media": ["made-up-token"],
            "targets": ["telegram:channel:-1001"],
        },
    )

    assert response.status_code == 400
    assert dispatched == []


def test_an_empty_post_is_refused(
    signed_in: TestClient,
    dispatched: list[tuple[int, ...]],
) -> None:
    response = signed_in.post(
        "/api/posts",
        json={"caption": "   ", "media": [], "targets": ["telegram:channel:-1001"]},
    )

    assert response.status_code == 400
    assert dispatched == []


def test_instagram_still_refuses_a_post_without_media(
    signed_in: TestClient,
    dispatched: list[tuple[int, ...]],
) -> None:
    response = signed_in.post(
        "/api/posts",
        json={
            "caption": "Только текст",
            "media": [],
            "targets": ["instagram:feed:self"],
        },
    )

    assert response.status_code == 400
    assert "фото или видео" in response.json()["detail"]
    assert dispatched == []


def test_history_lists_a_published_post(
    signed_in: TestClient,
    media_root: Path,
    dispatched: list[tuple[int, ...]],
) -> None:
    uploaded = _upload(signed_in)
    created = signed_in.post(
        "/api/posts",
        json={
            "caption": "Пост для истории",
            "media": [uploaded["token"]],
            "targets": ["telegram:channel:-1001"],
        },
    ).json()

    history = signed_in.get("/history")
    details = signed_in.get(f"/posts/{created['post_id']}")

    assert history.status_code == 200
    assert "Пост для истории" in history.text
    assert details.status_code == 200
    assert "Telegram" in details.text


def test_failed_jobs_can_be_requeued(
    signed_in: TestClient,
    media_root: Path,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    created = signed_in.post(
        "/api/posts",
        json={
            "caption": "Пост",
            "media": [],
            "targets": ["telegram:channel:-1001"],
        },
    ).json()
    post_id = created["post_id"]

    with session_factory.begin() as session:
        job = session.scalars(select(PublishJob)).one()
        job.status = "failed"
        job.attempts = 3
        session.get(Post, post_id).status = "failed"

    dispatched.clear()
    response = signed_in.post(f"/api/posts/{post_id}/retry")

    assert response.status_code == 200
    with session_factory() as session:
        job = session.scalars(select(PublishJob)).one()
        post = session.get(Post, post_id)

    assert job.status == "pending"
    assert job.attempts == 0
    assert post.status == "queued"
    assert dispatched == [(job.id,)]


def test_a_post_still_in_the_queue_cannot_be_deleted(
    signed_in: TestClient,
    dispatched: list[tuple[int, ...]],
) -> None:
    created = signed_in.post(
        "/api/posts",
        json={"caption": "Пост", "media": [], "targets": ["telegram:channel:-1001"]},
    ).json()

    response = signed_in.post(f"/api/posts/{created['post_id']}/delete")

    assert response.status_code == 409


def test_a_finished_post_is_deleted_with_its_files(
    signed_in: TestClient,
    media_root: Path,
    session_factory: sessionmaker[Session],
    dispatched: list[tuple[int, ...]],
) -> None:
    uploaded = _upload(signed_in)
    created = signed_in.post(
        "/api/posts",
        json={
            "caption": "Пост",
            "media": [uploaded["token"]],
            "targets": ["telegram:channel:-1001"],
        },
    ).json()

    with session_factory.begin() as session:
        session.scalars(select(PublishJob)).one().status = "done"

    response = signed_in.post(f"/api/posts/{created['post_id']}/delete")

    assert response.status_code == 204
    with session_factory() as session:
        assert session.get(Post, created["post_id"]) is None
    assert list(media_root.iterdir()) == []


def test_the_health_endpoint_needs_no_session(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
