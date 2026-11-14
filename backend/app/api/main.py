"""FastAPI application."""

import logging
import uuid

from fastapi import Depends, FastAPI
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


@app.post("/api/system/ping-task")
def ping_task(user_id: uuid.UUID = Depends(current_user_id)):
    """Phase 0 proof: run a background task that calls the model and writes an audit row."""
    return {"queued": True, "result": enqueue(jobs.ping_llm, str(user_id))}
