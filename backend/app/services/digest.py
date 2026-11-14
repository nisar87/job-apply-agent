"""Weekly summary for each user (master document, section 2)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import system_session, user_session
from app.models import Application, Campaign, Match
from app.services.notify import notify


def build(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=7)

    def count(*conds) -> int:
        return int(session.execute(select(func.count()).select_from(Application).where(Application.user_id == user_id, *conds)).scalar() or 0)

    return {
        "checked": int(session.execute(select(func.count()).select_from(Match).where(Match.user_id == user_id, Match.created_at >= since)).scalar() or 0),
        "applied": count(Application.sent_at >= since),
        "interviews": count(Application.status == "interview", Application.updated_at >= since),
        "offers": count(Application.status == "offer", Application.updated_at >= since),
        "rejections": count(Application.status == "rejected", Application.updated_at >= since),
        "waiting_for_you": count(Application.status == "awaiting_approval"),
    }


def send_for_user(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> dict:
    d = build(session, user_id, now)
    body = (f"Postings checked: {d['checked']}\nApplications sent: {d['applied']}\nInterviews: {d['interviews']}\n"
            f"Offers: {d['offers']}\nRejections: {d['rejections']}\nDrafts waiting for you: {d['waiting_for_you']}")
    notify(session, user_id, "digest", "Your week in applications", body, "/")
    return d


def send_all() -> int:
    with system_session() as s:
        users = list(s.execute(select(Campaign.user_id).distinct()).scalars())
    for user_id in users:
        with user_session(user_id) as s:
            send_for_user(s, user_id)
    return len(users)
