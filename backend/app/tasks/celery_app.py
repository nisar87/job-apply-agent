"""Celery application and schedule. Workers: `celery -A app.tasks.celery_app worker -B`."""

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()
celery_app = Celery("jobagent", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=settings.agent_run_timeout_seconds + 60,
    timezone="UTC",
    imports=("app.tasks.jobs",),
    beat_schedule={
        # Every tick decides per user and campaign what is due; the tasks themselves are idempotent.
        "scheduler-tick": {"task": "jobs.scheduler_tick", "schedule": 300.0},
        "renew-mail-watches": {"task": "jobs.renew_mail_watches", "schedule": crontab(hour=3, minute=17)},
        "allocate-posting-places": {"task": "jobs.allocate_places", "schedule": crontab(hour=0, minute=5)},
        "weekly-digest": {"task": "jobs.weekly_digest", "schedule": crontab(day_of_week=1, hour=6, minute=30)},
    },
)
