from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from loguru import logger

from app.core.drafts import DraftMedia, PostDraft
from app.database.repositories.posts_repo import PostRepository
from app.services.dispatch_service import dispatch_jobs
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
    TargetsOut,
)
from app.web.security import UploadTokenError

router = APIRouter(prefix="/api")

UPLOAD_CHUNK_SIZE = 1024 * 1024
UPLOADED_FILE = File(...)


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
        submission = await asyncio.to_thread(save_submission, draft, targets, factory)
    except SubmissionError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except Exception as error:
        logger.exception("Failed to persist a post from the control panel")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Не удалось сохранить пост. Попробуйте ещё раз.",
        ) from error

    result = await asyncio.to_thread(dispatch_jobs, submission.job_ids)
    return PostCreatedOut(
        post_id=submission.post_id,
        job_count=len(submission.job_ids),
        dispatched=len(result.dispatched),
        failed=len(result.failed),
    )


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
        await asyncio.to_thread(dispatch_jobs, tuple(job_ids))
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


async def _chunks(file: UploadFile) -> AsyncIterator[bytes]:
    while True:
        chunk = await file.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            return
        yield chunk
