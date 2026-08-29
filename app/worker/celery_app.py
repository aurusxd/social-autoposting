import os
from pathlib import Path

from celery import Celery

from app.core.config import load_environment

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_environment(PROJECT_ROOT / ".env")
BROKER_URL = os.getenv("CELERY_BROKER_URL", "").strip() or "redis://localhost:6379/0"
# How often beat looks for posts whose scheduled time has come. The delay a
# user sees is at most this interval, so keep it well below a minute.
SCHEDULER_INTERVAL_SECONDS = int(
    os.getenv("SCHEDULER_INTERVAL_SECONDS", "").strip() or "30"
)

celery = Celery(
    "social_autoposting",
    broker=BROKER_URL,
    include=["app.worker.tasks"],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_timeout=3,
    broker_connection_retry_on_startup=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "release-due-posts": {
            "task": "worker.release_due_posts",
            "schedule": SCHEDULER_INTERVAL_SECONDS,
            "options": {"expires": SCHEDULER_INTERVAL_SECONDS},
        },
    },
)
