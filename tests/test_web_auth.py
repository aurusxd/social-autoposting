from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import AppConfig
from app.core.security import hash_password
from app.web.main import create_app
from app.web.security import MAX_FAILED_LOGINS, SESSION_COOKIE
from tests.factories import PASSWORD, app_config, web_config


def test_pages_send_anonymous_visitors_to_the_login_form(client: TestClient) -> None:
    for path in ("/", "/history", "/posts/1"):
        response = client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_the_api_answers_anonymous_calls_with_json(client: TestClient) -> None:
    response = client.get("/api/targets")

    assert response.status_code == 401
    assert response.json()["detail"]


def test_a_wrong_password_is_refused(client: TestClient) -> None:
    response = client.post("/login", data={"username": "admin", "password": "нет"})

    assert response.status_code == 401
    assert "Неверный логин или пароль" in response.text
    assert not client.cookies.get(SESSION_COOKIE)


def test_a_wrong_username_is_refused(client: TestClient) -> None:
    response = client.post("/login", data={"username": "root", "password": PASSWORD})

    assert response.status_code == 401
    assert not client.cookies.get(SESSION_COOKIE)


def test_signing_in_opens_the_panel(signed_in: TestClient) -> None:
    response = signed_in.get("/")

    assert response.status_code == 200
    assert "Основной канал" in response.text


def test_a_hashed_password_also_signs_in(
    session_factory: sessionmaker[Session],
) -> None:
    config = app_config(
        web=web_config(password="", password_hash=hash_password("тайна", 1_000))
    )
    with TestClient(
        create_app(config=config, session_factory=session_factory),
        follow_redirects=False,
    ) as client:
        assert (
            client.post(
                "/login", data={"username": "admin", "password": "тайна"}
            ).status_code
            == 303
        )


def test_repeated_failures_lock_the_form_for_a_while(client: TestClient) -> None:
    for _ in range(MAX_FAILED_LOGINS):
        client.post("/login", data={"username": "admin", "password": "нет"})

    # Even the correct password has to wait out the lockout.
    response = client.post("/login", data={"username": "admin", "password": PASSWORD})

    assert response.status_code == 401
    assert "Слишком много попыток" in response.text


def test_logging_out_drops_the_session(signed_in: TestClient) -> None:
    response = signed_in.post("/logout")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert signed_in.get("/").status_code == 303


def test_a_cookie_signed_with_another_key_is_ignored(
    config: AppConfig,
    session_factory: sessionmaker[Session],
) -> None:
    stranger = create_app(
        config=app_config(
            web=web_config(secret_key="a-completely-different-secret-key")
        ),
        session_factory=session_factory,
    )
    with TestClient(stranger, follow_redirects=False) as other:
        other.post("/login", data={"username": "admin", "password": PASSWORD})
        forged = other.cookies.get(SESSION_COOKIE)

    with TestClient(
        create_app(config=config, session_factory=session_factory),
        follow_redirects=False,
    ) as client:
        client.cookies.set(SESSION_COOKIE, forged)
        assert client.get("/").status_code == 303


def test_the_session_cookie_is_locked_down(signed_in: TestClient) -> None:
    header = signed_in.post(
        "/login", data={"username": "admin", "password": PASSWORD}
    ).headers["set-cookie"]

    assert "HttpOnly" in header
    assert "SameSite=lax" in header
