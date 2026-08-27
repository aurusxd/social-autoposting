from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import AppConfig
from app.web.security import SessionManager


class NotAuthenticated(Exception):
    """Raised when a request carries no valid session cookie."""


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_sessions(request: Request) -> SessionManager:
    return request.app.state.sessions


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory


def get_db(
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_user(
    request: Request,
    sessions: Annotated[SessionManager, Depends(get_sessions)],
) -> str:
    user = sessions.read(request)
    if user is None:
        raise NotAuthenticated
    return user


CurrentUser = Annotated[str, Depends(require_user)]
Factory = Annotated["sessionmaker[Session]", Depends(get_session_factory)]
Config = Annotated[AppConfig, Depends(get_config)]
Sessions = Annotated[SessionManager, Depends(get_sessions)]
Templates = Annotated[Jinja2Templates, Depends(get_templates)]
Database = Annotated[Session, Depends(get_db)]
