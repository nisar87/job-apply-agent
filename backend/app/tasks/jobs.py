"""Celery tasks. Each is a thin wrapper that opens a session and calls the service layer."""

import uuid

from app import audit
from app.db import user_session
from app.llm.access import llm_for
from app.runs import finish_run, start_run
from app.tasks.celery_app import celery_app
from app.tasks.locks import run_lock


@celery_app.task(name="jobs.ping_llm")
def ping_llm(user_id: str) -> dict:
    """Phase 0 proof: a background task that calls the model and writes an audit row."""
    uid = uuid.UUID(user_id)
    with run_lock(uid, "ping"), user_session(uid) as session:
        run = start_run(session, uid, "ping")
        llm = llm_for(session, uid)
        result = llm.generate("Reply with the single word: pong", task="ping")
        audit.record(
            session, user_id=uid, actor="system", tool="ping_llm", input={}, run_id=run.id,
            result={"text": result.text[:100]}, reason="phase 0 health check", cost_usd=llm.run_cost,
        )
        finish_run(session, run, summary=result.text[:100], cost_usd=llm.run_cost)
        return {"run_id": str(run.id), "text": result.text}


@celery_app.task(name="jobs.tick_user", soft_time_limit=1500, time_limit=1700)
def tick_user_task(user_id: str, now_iso: str | None = None) -> dict:
    """One user's whole pass (discovery, drafting, sending, inbox) as its own queued job. A second job for the
    same user while one runs does nothing, and any failure is recorded for the operator, never raised to the others."""
    import time
    from datetime import UTC, datetime

    from app.connectors.fetcher import Fetcher
    from app.tasks import scheduler
    from app.tasks.locks import LockBusy

    uid = uuid.UUID(user_id)
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(UTC)
    started, t0 = datetime.now(UTC), time.monotonic()
    try:
        with run_lock(uid, "tick", ttl_seconds=1700):
            result = scheduler.tick_user(uid, now, fetcher=Fetcher())
    except LockBusy:
        return {"busy": True, "sent": 0}
    except Exception as exc:  # noqa: BLE001 - recorded, and the other users' jobs carry on
        scheduler.log.exception("tick failed for user %s", user_id)
        scheduler.record_health(uid, started, time.monotonic() - t0, [f"{type(exc).__name__}: {exc}"[:200]])
        return {"failed": True, "sent": 0}
    scheduler.record_health(uid, started, time.monotonic() - t0, list(result.get("errors", [])))
    return result


@celery_app.task(name="jobs.scheduler_tick")
def scheduler_tick() -> dict:
    from app.tasks import scheduler

    return scheduler.tick()


@celery_app.task(name="jobs.allocate_places")
def allocate_places() -> int:
    from app.tasks import scheduler

    return scheduler.allocate_places()


@celery_app.task(name="jobs.renew_mail_watches")
def renew_mail_watches() -> int:
    from app.services import inbox

    return inbox.renew_watches()


@celery_app.task(name="jobs.weekly_digest")
def weekly_digest() -> int:
    from app.services import digest

    return digest.send_all()
