from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import AppConfig, load_config
from app.database.database import SessionLocal
from app.web.dependencies import NotAuthenticated
from app.web.routers import api, auth, pages
from app.web.security import SessionManager

WEB_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_ROOT / "templates"
STATIC_DIR = WEB_ROOT / "static"


def create_app(
    config: AppConfig | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    application = FastAPI(
        title="Панель автопостинга",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.config = config or load_config()
    application.state.sessions = SessionManager(application.state.config.web)
    application.state.session_factory = session_factory or SessionLocal
    application.state.templates = _build_templates()

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.include_router(auth.router)
    application.include_router(pages.router)
    application.include_router(api.router)

    @application.exception_handler(NotAuthenticated)
    async def _unauthenticated(request: Request, _: NotAuthenticated) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"detail": "Сессия истекла, войдите заново"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @application.middleware("http")
    async def _security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    return application


def _build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.globals["site_name"] = "Автопостинг"
    return templates


__all__ = ["create_app"]
