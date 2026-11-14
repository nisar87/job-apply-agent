"""Bookkeeping for agent and system runs."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Run


def start_run(session: Session, user_id: uuid.UUID, run_type: str, campaign_id: uuid.UUID | None = None) -> Run:
    run = Run(user_id=user_id, type=run_type, campaign_id=campaign_id, state="running")
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run: Run, state: str = "done", summary: str = "", cost_usd: float = 0.0) -> Run:
    run.state = state
    run.summary = summary
    run.cost_usd = cost_usd
    run.finished_at = datetime.now(UTC)
    session.flush()
    return run
