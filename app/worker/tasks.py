from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from celery.signals import worker_ready
from loguru import logger

from app.core.config import load_config
from app.database.database import SessionLocal
from app.database.repositories.publish_jobs_repo import PublishJobRepository
from app.publishers import (
    MediaFile,
    Post,
    PublisherError,
    PublishResult,
    PublishTarget,
)
from app.publishers.factory import build_publishers
from app.worker.celery_app import celery

RETRY_DELAYS = {1: 10, 2: 60}
STALE_JOB_TIMEOUT_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: int
    post_id: int
    platform: str
    post: Post
    target: PublishTarget


@dataclass(frozen=True, slots=True)
class JobOutcome:
    state: Literal["done", "retry", "failed", "ignored"]
    attempt: int | None = None
    error: str | None = None
    retry_after: int | None = None


@celery.task(name="worker.healthcheck")
def healthcheck() -> None:
    logger.info("Celery worker is alive")


@celery.task(bind=True, name="worker.publish_job", max_retries=2)
def publish_job(self, job_id: int) -> None:
    try:
        outcome = process_publish_job(job_id)
    except Exception as error:
        logger.exception("Failed to process publish job {}", job_id)
        delay = (10, 60)[min(self.request.retries, 1)]
        raise self.retry(exc=error, countdown=delay) from error

    if outcome.state != "retry":
        return

    attempt = outcome.attempt or 1
    delay = outcome.retry_after or RETRY_DELAYS[attempt]
    error = RuntimeError(outcome.error or "Temporary publisher error")
    logger.warning("Publish job {} will retry in {} seconds", job_id, delay)
    raise self.retry(exc=error, countdown=delay)


def process_publish_job(job_id: int) -> JobOutcome:
    claimed_job = claim_job(job_id)
    if claimed_job is None:
        logger.info("Publish job {} is already claimed or completed", job_id)
        return JobOutcome("ignored")

    result = run_publisher(claimed_job)
    error_text = result.error or "Publisher returned an unsuccessful result"

    with SessionLocal.begin() as session:
        repository = PublishJobRepository(session)
        if result.success:
            repository.mark_done(job_id)
            repository.refresh_post_status(claimed_job.post_id)
            logger.info("Publish job {} completed", job_id)
            return JobOutcome("done")

        if result.retryable:
            attempt = repository.schedule_retry(job_id, error_text)
            if attempt is not None:
                return JobOutcome(
                    "retry",
                    attempt=attempt,
                    error=error_text,
                    retry_after=result.retry_after,
                )

        repository.mark_failed(job_id, error_text)
        repository.refresh_post_status(claimed_job.post_id)
        logger.error("Publish job {} failed: {}", job_id, error_text)
        return JobOutcome("failed", error=error_text)


def claim_job(job_id: int) -> ClaimedJob | None:
    with SessionLocal.begin() as session:
        repository = PublishJobRepository(session)
        job = repository.claim_pending(job_id)
        if job is None:
            return None

        post_record = job.post
        media_files = tuple(
            MediaFile(
                file_path=media.file_path,
                media_type=media.media_type,
                tg_file_id=media.tg_file_id,
                position=media.position,
            )
            for media in sorted(
                post_record.media_files,
                key=lambda item: item.position,
            )
        )
        return ClaimedJob(
            id=job.id,
            post_id=post_record.id,
            platform=job.platform,
            post=Post(
                id=post_record.id,
                caption=post_record.caption,
                media_files=media_files,
            ),
            target=PublishTarget(
                key=job.target_key,
                kind=job.target_kind,
                name=job.target_key,
            ),
        )


def run_publisher(job: ClaimedJob) -> PublishResult:
    try:
        config = load_config()
        publisher = build_publishers(config).get(job.platform)
        if publisher is None:
            return PublishResult(
                success=False,
                retryable=False,
                error=f"Publisher is not implemented: {job.platform}",
            )
        return asyncio.run(publisher.publish(job.post, job.target))
    except PublisherError as error:
        return PublishResult(success=False, retryable=False, error=str(error))
    except Exception as error:
        logger.exception("Unexpected publisher error for job {}", job.id)
        return PublishResult(success=False, retryable=True, error=str(error))


@worker_ready.connect
def dispatch_pending_on_worker_start(**_: object) -> None:
    with SessionLocal.begin() as session:
        repository = PublishJobRepository(session)
        recovered_ids = repository.recover_stale(STALE_JOB_TIMEOUT_SECONDS)
        pending_ids = repository.pending_ids()

    job_ids = tuple(dict.fromkeys((*recovered_ids, *pending_ids)))
    for job_id in job_ids:
        publish_job.apply_async(
            args=(job_id,),
            task_id=f"publish-job-{job_id}",
            retry=False,
        )

    logger.info("Dispatched {} pending jobs on worker start", len(job_ids))
