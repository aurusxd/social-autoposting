from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse

from app.core.drafts import MEDIA_LIMIT, DraftMedia
from app.core.scheduling import MOSCOW_LABEL, format_moscow, moscow_input_value
from app.database.models import MediaFile, Post
from app.database.repositories.posts_repo import PostRepository
from app.services.media_storage import resolve_media_path
from app.services.target_registry import resolve_targets
from app.web.dependencies import Config, CurrentUser, Database, Sessions, Templates
from app.web.presenters import group_targets, platform_label, status_label
from app.web.security import UploadTokenError

router = APIRouter()

PAGE_SIZE = 20
POST_STATUSES = ("scheduled", "queued", "done", "failed")


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
async def composer(
    request: Request,
    user: CurrentUser,
    config: Config,
    templates: Templates,
) -> HTMLResponse:
    resolved = await resolve_targets(config)
    return templates.TemplateResponse(
        request,
        "composer.html",
        {
            "user": user,
            "active": "composer",
            "groups": group_targets(resolved.targets),
            "whatsapp_failed": resolved.whatsapp_failed,
            "truncated": resolved.truncated,
            "media_limit": MEDIA_LIMIT,
            "max_upload_mb": config.web.max_upload_bytes // 1024**2,
            "timezone_label": MOSCOW_LABEL,
        },
    )


@router.get("/history", response_class=HTMLResponse)
async def history(
    request: Request,
    user: CurrentUser,
    database: Database,
    templates: Templates,
    page: int = Query(default=1, ge=1),
    post_status: str | None = Query(default=None, alias="status"),
) -> HTMLResponse:
    selected_status = post_status if post_status in POST_STATUSES else None
    repository = PostRepository(database)
    total = repository.count_posts(selected_status)
    posts = repository.list_posts(
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
        status=selected_status,
    )
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "user": user,
            "active": "history",
            "posts": [_post_summary(post) for post in posts],
            "page": page,
            "has_next": page * PAGE_SIZE < total,
            "total": total,
            "selected_status": selected_status,
            "statuses": [
                {"value": value, "label": status_label(value)}
                for value in POST_STATUSES
            ],
        },
    )


@router.get("/posts/{post_id}", response_class=HTMLResponse)
async def post_details(
    request: Request,
    user: CurrentUser,
    database: Database,
    templates: Templates,
    post_id: int,
) -> HTMLResponse:
    post = PostRepository(database).get_post(post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пост не найден")
    return templates.TemplateResponse(
        request,
        "post.html",
        {
            "user": user,
            "active": "history",
            "post": _post_summary(post),
            "timezone_label": MOSCOW_LABEL,
            "schedule_input": moscow_input_value(post.scheduled_at),
            "media": [
                {
                    "id": media.id,
                    "media_type": media.media_type,
                    "url": f"/posts/{post.id}/media/{media.id}",
                }
                for media in sorted(post.media_files, key=lambda item: item.position)
            ],
            "jobs": [
                {
                    "id": job.id,
                    "platform": platform_label(job.platform),
                    "target": job.target_key,
                    "kind": job.target_kind,
                    "status": job.status,
                    "status_label": status_label(job.status),
                    "attempts": job.attempts,
                    "last_error": job.last_error,
                }
                for job in sorted(post.publish_jobs, key=lambda item: item.id)
            ],
        },
    )


@router.get("/media/{token}")
async def pending_media(
    user: CurrentUser,
    sessions: Sessions,
    token: str,
) -> FileResponse:
    del user
    try:
        media = DraftMedia.from_dict(sessions.load_upload(token))
    except (UploadTokenError, KeyError, ValueError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден") from error
    return _file_response(media.file_path, media.file_name)


@router.get("/posts/{post_id}/media/{media_id}")
async def stored_media(
    user: CurrentUser,
    database: Database,
    post_id: int,
    media_id: int,
) -> FileResponse:
    del user
    media = database.get(MediaFile, media_id)
    if media is None or media.post_id != post_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    return _file_response(media.file_path)


def _file_response(relative_path: str, download_name: str = "") -> FileResponse:
    path = resolve_media_path(relative_path)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        filename=download_name or None,
        content_disposition_type="inline",
    )


def _post_summary(post: Post) -> dict[str, object]:
    jobs = list(post.publish_jobs)
    media_files = list(post.media_files)
    caption = post.caption or ""
    return {
        "id": post.id,
        "created_at": post.created_at,
        "created_label": format_moscow(post.created_at),
        "scheduled_at": post.scheduled_at,
        "scheduled_label": format_moscow(post.scheduled_at),
        "status": post.status,
        "status_label": status_label(post.status),
        "caption": caption,
        "preview": caption[:180] + ("…" if len(caption) > 180 else ""),
        "photo_count": sum(item.media_type == "photo" for item in media_files),
        "video_count": sum(item.media_type == "video" for item in media_files),
        "job_count": len(jobs),
        "done_count": sum(job.status == "done" for job in jobs),
        "failed_count": sum(job.status == "failed" for job in jobs),
        "platforms": sorted({platform_label(job.platform) for job in jobs}),
    }
