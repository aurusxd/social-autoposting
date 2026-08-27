from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger

from app.web.dependencies import Sessions, Templates
from app.web.security import client_key

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    sessions: Sessions,
    templates: Templates,
) -> HTMLResponse:
    if sessions.read(request) is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None},
    )


@router.post("/login", response_model=None)
async def login(
    request: Request,
    sessions: Sessions,
    templates: Templates,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    client = client_key(request)
    locked_for = sessions.lockout_seconds_left(client)
    if locked_for:
        minutes = max(1, locked_for // 60)
        return _rejected(
            request,
            templates,
            f"Слишком много попыток. Повторите через {minutes} мин.",
        )

    if not sessions.authenticate(username.strip(), password):
        sessions.register_failure(client)
        logger.warning("Rejected a control panel login from {}", client)
        return _rejected(request, templates, "Неверный логин или пароль")

    sessions.register_success(client)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    sessions.issue(response, sessions.config.username)
    logger.info("Control panel login from {}", client)
    return response


@router.post("/logout")
async def logout(sessions: Sessions) -> RedirectResponse:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    sessions.clear(response)
    return response


def _rejected(request: Request, templates: Templates, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": message},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )
