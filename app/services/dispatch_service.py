from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from app.worker.tasks import publish_job


@dataclass(frozen=True, slots=True)
class DispatchResult:
    dispatched: tuple[int, ...]
    failed: tuple[int, ...]


def dispatch_jobs(job_ids: tuple[int, ...]) -> DispatchResult:
    dispatched = []
    failed = []
    for job_id in job_ids:
        try:
            publish_job.apply_async(
                args=(job_id,),
                task_id=f"publish-job-{job_id}",
                retry=False,
            )
        except Exception:
            logger.exception("Failed to dispatch publish job {}", job_id)
            failed.append(job_id)
        else:
            dispatched.append(job_id)
    return DispatchResult(tuple(dispatched), tuple(failed))
