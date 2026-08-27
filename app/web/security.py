from __future__ import annotations

import hmac
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import WebConfig
from app.core.security import verify_password_hash

SESSION_COOKIE = "sap_session"
SESSION_SALT = "panel-session"
UPLOAD_SALT = "panel-upload"

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300


class UploadTokenError(ValueError):
    """Raised when an upload token is missing, forged or expired."""


@dataclass(slots=True)
class _LoginAttempts:
    failures: int = 0
    locked_until: float = 0.0


@dataclass(slots=True)
class SessionManager:
    """Signed cookie sessions plus signed handles for uploaded files.

    The cookie is `SameSite=Lax`, so a browser never attaches it to a POST
    started by another site; that is what keeps the panel safe from CSRF
    without a per-form token.
    """

    config: WebConfig
    _attempts: dict[str, _LoginAttempts] = field(default_factory=dict)

    @property
    def _sessions(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.config.secret_key, salt=SESSION_SALT)

    @property
    def _uploads(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.config.secret_key, salt=UPLOAD_SALT)

    def authenticate(self, username: str, password: str) -> bool:
        # compare_digest refuses non-ASCII text, and a password may well hold
        # Cyrillic, so both sides are compared as UTF-8 bytes.
        expected_user = hmac.compare_digest(
            username.encode("utf-8"),
            self.config.username.encode("utf-8"),
        )
        if self.config.password_hash:
            expected_password = verify_password_hash(
                self.config.password_hash,
                password,
            )
        else:
            expected_password = hmac.compare_digest(
                password.encode("utf-8"),
                self.config.password.encode("utf-8"),
            )
        # Both comparisons always run so a wrong name and a wrong password
        # cannot be told apart by how long the answer takes.
        return expected_user and expected_password

    def lockout_seconds_left(self, client: str, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else now
        attempts = self._attempts.get(client)
        if attempts is None or attempts.locked_until <= moment:
            return 0
        return int(attempts.locked_until - moment) + 1

    def register_failure(self, client: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        attempts = self._attempts.setdefault(client, _LoginAttempts())
        attempts.failures += 1
        if attempts.failures >= MAX_FAILED_LOGINS:
            attempts.failures = 0
            attempts.locked_until = moment + LOCKOUT_SECONDS

    def register_success(self, client: str) -> None:
        self._attempts.pop(client, None)

    def issue(self, response: Response, username: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            self._sessions.dumps({"user": username}),
            max_age=self.config.session_max_age,
            httponly=True,
            samesite="lax",
            secure=self.config.secure_cookies,
            path="/",
        )

    def clear(self, response: Response) -> None:
        response.delete_cookie(SESSION_COOKIE, path="/")

    def read(self, request: Request) -> str | None:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return None
        try:
            payload = self._sessions.loads(raw, max_age=self.config.session_max_age)
        except (BadSignature, SignatureExpired):
            return None
        user = payload.get("user") if isinstance(payload, dict) else None
        if not isinstance(user, str) or user != self.config.username:
            return None
        return user

    def sign_upload(self, payload: dict[str, Any]) -> str:
        return self._uploads.dumps(payload)

    def load_upload(self, token: str, max_age: int = 24 * 3600) -> dict[str, Any]:
        try:
            payload = self._uploads.loads(token, max_age=max_age)
        except SignatureExpired as error:
            raise UploadTokenError("Файл слишком долго ждал отправки") from error
        except BadSignature as error:
            raise UploadTokenError("Файл не найден") from error
        if not isinstance(payload, dict):
            raise UploadTokenError("Файл не найден")
        return payload


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"
