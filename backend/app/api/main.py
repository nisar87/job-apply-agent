"""FastAPI application."""

import hmac
import importlib
import logging
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import account, auth, core, replies, work
from app.api.deps import current_user_id
from app.config import get_settings
from app.db import system_session
from app.tasks import jobs
from app.tasks.dispatch import enqueue
from app.tasks.locks import get_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Job Apply Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_url],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    if get_settings().is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    if get_settings().hide_from_search:
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    return response


app.include_router(auth.router)
app.include_router(core.router)
app.include_router(work.router)
app.include_router(replies.router)
app.include_router(account.router)


@app.get("/api/health")
def health():
    """Liveness plus the two dependencies every request needs."""
    status = {"api": "ok", "database": "ok", "redis": "ok"}
    try:
        with system_session() as session:
            session.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        status["database"] = f"error: {type(exc).__name__}"
    try:
        get_redis().ping()
    except Exception as exc:  # noqa: BLE001
        status["redis"] = f"error: {type(exc).__name__}"
    status["ok"] = all(v == "ok" for k, v in status.items())
    return status


SCHEDULED_JOBS = {
    "tick": ("app.tasks.scheduler", "tick"),                # every 5 minutes
    "allocate": ("app.tasks.scheduler", "allocate_places"),  # once a day
    "renew-watches": ("app.services.inbox", "renew_watches"),  # once a day
    "digest": ("app.services.digest", "send_all"),          # once a week
}


@app.post("/api/system/run/{job}")
def run_scheduled_job(job: str, x_scheduler_token: str = Header(default="")):
    """Scheduled work for deployments without a Celery worker: Cloud Scheduler calls this with the
    shared token. Each job is safe to repeat (runs are locked per user, sends are idempotent)."""
    token = get_settings().scheduler_token
    if not token or not hmac.compare_digest(x_scheduler_token, token):
        raise HTTPException(403, "Not allowed")
    if job not in SCHEDULED_JOBS:
        raise HTTPException(404, "Unknown job")
    module, name = SCHEDULED_JOBS[job]
    result = getattr(importlib.import_module(module), name)()
    return {"job": job, "result": result}


@app.post("/api/system/ping-task")
def ping_task(user_id: uuid.UUID = Depends(current_user_id)):
    """Phase 0 proof: run a background task that calls the model and writes an audit row."""
    return {"queued": True, "result": enqueue(jobs.ping_llm, str(user_id))}
