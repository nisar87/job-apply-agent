"""FastAPI application."""

import hmac
import importlib
import logging
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api import account, admin, auth, core, replies, work
from app.api.deps import current_user_id
from app.config import get_settings
from app.db import system_session
from app.tasks import jobs
from app.tasks.dispatch import enqueue
from app.tasks.locks import get_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# The HTTP libraries log every address they call at INFO, keys in query strings included. Keep them to warnings.
for noisy in ("httpx", "httpx2", "httpcore", "google_genai.models"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger(__name__)
app = FastAPI(title="Job Apply Agent", version="0.1.0")


@app.exception_handler(genai_errors.APIError)
def gemini_refused(request, exc: genai_errors.APIError):
    """Say what Gemini said in plain words instead of a bare 500."""
    code = int(getattr(exc, "code", 0) or 0)
    log.warning("Gemini refused a call (%s): %s", code, str(exc)[:300])
    if code in (402, 429) or "RESOURCE_EXHAUSTED" in str(exc):
        text, status = ("The Gemini key has no credit left or has reached its limit. Add credit in Google AI Studio "
                        "(ai.studio/projects), or save a different key under Account and keys, then try again."), 402
    elif code in (400, 401, 403) and ("API key" in str(exc) or code in (401, 403)):
        text, status = "Google did not accept the Gemini key. Check it under Account and keys.", 400
    else:
        text, status = "Gemini is not answering right now. Try again in a minute.", 503
    return JSONResponse({"detail": text}, status_code=status)


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
app.include_router(admin.router)


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
    "harvest": ("app.services.harvest", "run"),             # optional: tick already starts it every two hours
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


class BoardLinksIn(BaseModel):
    links: list[str] = Field(max_length=25000)
    source: str = Field(default="wayback", max_length=40)


@app.post("/api/system/boards")
def add_company_boards(body: BoardLinksIn, x_scheduler_token: str = Header(default="")):
    """Company careers boards found outside this service (scripts/discover_boards.py: the Internet Archive refuses
    connections from Google Cloud). Only hiring-system board addresses are taken from the links; known boards are
    left as they are. Same shared token as the scheduled jobs."""
    token = get_settings().scheduler_token
    if not token or not hmac.compare_digest(x_scheduler_token, token):
        raise HTTPException(403, "Not allowed")
    from app.services import harvest, sites

    boards = sites.find_company_boards(*[link[:500] for link in body.links])
    with system_session() as session:
        before = harvest._company_boards(session)
        sites.add(session, boards, body.source)
        session.commit()
        # Counted, not taken from the insert: a skipped known board does not reliably report 0 rows.
        new = harvest._company_boards(session) - before
    return {"boards": len(boards), "new": new}


@app.post("/api/system/ping-task")
def ping_task(user_id: uuid.UUID = Depends(current_user_id)):
    """Phase 0 proof: run a background task that calls the model and writes an audit row."""
    return {"queued": True, "result": enqueue(jobs.ping_llm, str(user_id))}
