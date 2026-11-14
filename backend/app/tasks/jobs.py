"""Celery tasks. Each is a thin wrapper that opens a session and calls the service layer."""

import uuid

from app import audit
from app.db import user_session
from app.llm import MeteredLLM, get_llm
from app.runs import finish_run, start_run
from app.tasks.celery_app import celery_app
from app.tasks.locks import run_lock


@celery_app.task(name="jobs.ping_llm")
def ping_llm(user_id: str) -> dict:
    """Phase 0 proof: a background task that calls the model and writes an audit row."""
    uid = uuid.UUID(user_id)
    with run_lock(uid, "ping"), user_session(uid) as session:
        run = start_run(session, uid, "ping")
        llm = MeteredLLM(get_llm(), session, uid)
        result = llm.generate("Reply with the single word: pong", task="ping")
        audit.record(
            session, user_id=uid, actor="system", tool="ping_llm", input={}, run_id=run.id,
            result={"text": result.text[:100]}, reason="phase 0 health check", cost_usd=llm.run_cost,
        )
        finish_run(session, run, summary=result.text[:100], cost_usd=llm.run_cost)
        return {"run_id": str(run.id), "text": result.text}
