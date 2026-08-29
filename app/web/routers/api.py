from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from loguru import logger

from app.core.drafts import DraftMedia, PostDraft
from app.core.scheduling import ScheduleError, normalize_schedule
from app.database.repositories.posts_repo import PostRepository
from app.services.dispatch_service import DispatchResult, dispatch_jobs
from app.services.media_storage import MediaError, delete_media, save_upload
from app.services.submission_service import SubmissionError, save_submission
from app.services.target_registry import resolve_targets
from app.web.dependencies import Config, CurrentUser, Database, Factory, Sessions
from app.web.presenters import (
    UnknownTargetError,
    format_size,
    group_targets,
    select_targets,
)
from app.web.schemas import (
    JobIdsOut,
    MediaOut,
    MediaTokenIn,
    PostCreatedOut,
    PostCreateIn,
    ScheduleIn,
    ScheduleOut,
    TargetsOut,
)
from app.web.security import UploadTokenError

router = APIRouter(prefix="/api")

UPLOAD_CHUNK_SIZE = 1024 * 1024
UPLOADED_FILE = File(...)
DISPATCH_TIMEOUT_SECONDS = 10


@router.get("/targets", response_model=TargetsOut)
async def read_targets(
    user: CurrentUser,
    config: Config,
    refresh: bool = Query(default=False),
) -> TargetsOut:
    del user
    resolved = await resolve_targets(config, refresh=refresh)
    return TargetsOut(
        groups=group_targets(resolved.targets),
        whatsapp_failed=resolved.whatsapp_failed,
        truncated=resolved.truncated,
    )


@router.post("/media", response_model=MediaOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    user: CurrentUser,
    config: Config,
    sessions: Sessions,
    file: UploadFile = UPLOADED_FILE,
) -> MediaOut:
    del user
    try:
        media = await save_upload(
            file.filename or "upload",
            _chunks(file),
            config.web.max_upload_bytes,
        )
    except MediaError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    token = sessions.sign_upload(media.to_dict())
    return MediaOut(
        token=token,
        file_name=media.file_name,
        media_type=media.media_type,
        size_bytes=media.size_bytes,
        size_label=format_size(media.size_bytes),
        preview_url=f"/media/{token}",
    )


@router.post("/media/delete", status_code=status.HTTP_204_NO_CONTENT)
async def remove_media(
    user: CurrentUser,
    sessions: Sessions,
    payload: MediaTokenIn,
) -> None:
    del user
    try:
        media = DraftMedia.from_dict(sessions.load_upload(payload.token))
    except (UploadTokenError, KeyError, ValueError):
        # A file the server cannot identify is already gone as far as the
        # panel is concerned, so removing it is a no-op rather than an error.
        return
    delete_media([media.file_path])


@router.post(
    "/posts",
    response_model=PostCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_post(
    user: CurrentUser,
    config: Config,
    sessions: Sessions,
    factory: Factory,
    payload: PostCreateIn,
) -> PostCreatedOut:
    del user
    try:
        media = tuple(
            DraftMedia.from_dict(sessions.load_upload(token)) for token in payload.media
        )
    except (UploadTokenError, KeyError, ValueError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    resolved = await resolve_targets(config)
    try:
        targets = select_targets(resolved.targets, payload.targets)
    except UnknownTargetError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error

    draft = PostDraft(caption=payload.caption.strip(), media=media)
    try:
        submission = await asyncio.to_thread(
            save_submission,
            draft,
            targets,
            factory,
            payload.scheduled_at,
        )
    except (SubmissionError, ScheduleError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except Exception as error:
        logger.exception("Failed to persist a post from the control panel")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Не удалось сохранить пост. Попробуйте ещё раз.",
        ) from error

    # A scheduled post waits in SQLite; beat hands it to Celery when it is due.
    result = (
        DispatchResult((), ())
        if submission.scheduled_at
        else await _dispatch(submission.job_ids)
    )
    return PostCreatedOut(
        post_id=submission.post_id,
        job_count=len(submission.job_ids),
        dispatched=len(result.dispatched),
        failed=len(result.failed),
        scheduled_at=submission.scheduled_at,
    )


@router.post("/posts/{post_id}/schedule", response_model=ScheduleOut)
async def reschedule_post(
    user: CurrentUser,
    database: Database,
    post_id: int,
    payload: ScheduleIn,
) -> ScheduleOut:
    """Move a waiting post to another time, or publish it immediately."""
    del user
    repository = PostRepository(database)
    post = repository.get_post(post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пост не найден")
    if post.status != "scheduled":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Пост уже не ждёт публикации",
        )

    try:
        planned_at = normalize_schedule(payload.scheduled_at)
    except ScheduleError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    if planned_at is None:
        job_ids = repository.publish_now(post_id)
        database.commit()
        if job_ids:
            await _dispatch(tuple(job_ids))
        return ScheduleOut(post_id=post_id, scheduled_at=None, job_ids=job_ids)

    if not repository.reschedule(post_id, planned_at):
        raise HTTPException(status.HTTP_409_CONFLICT, "Пост уже не ждёт публикации")
    database.commit()
    return ScheduleOut(post_id=post_id, scheduled_at=planned_at, job_ids=[])


@router.post("/posts/{post_id}/retry", response_model=JobIdsOut)
async def retry_post(
    user: CurrentUser,
    database: Database,
    post_id: int,
) -> JobIdsOut:
    del user
    repository = PostRepository(database)
    if repository.get_post(post_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пост не найден")
    job_ids = repository.requeue_failed(post_id)
    database.commit()
    if job_ids:
        await _dispatch(tuple(job_ids))
    return JobIdsOut(job_ids=job_ids)


@router.post("/posts/{post_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(
    user: CurrentUser,
    database: Database,
    post_id: int,
) -> None:
    del user
    repository = PostRepository(database)
    paths = repository.media_paths(post_id)
    if not repository.delete_post(post_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Пост нельзя удалить, пока его задания в работе",
        )
    database.commit()
    delete_media(paths)


async def _dispatch(job_ids: tuple[int, ...]) -> DispatchResult:
    """Hand jobs to Celery without letting a sick broker stall the request.

    The jobs are already saved as `pending`, and a worker re-dispatches every
    pending job when it starts, so giving up early loses nothing but keeps the
    page responsive while Redis is unreachable.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(dispatch_jobs, job_ids),
            timeout=DISPATCH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Celery did not accept jobs {} within {} seconds; "
            "they stay pending for the worker to pick up",
            job_ids,
            DISPATCH_TIMEOUT_SECONDS,
        )
        return DispatchResult((), job_ids)


async def _chunks(file: UploadFile) -> AsyncIterator[bytes]:
    while True:
        chunk = await file.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            return
        yield chunk
