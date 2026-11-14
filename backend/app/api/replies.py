"""Escalations (the inbox), the Gmail push webhook, daily statistics and the operator console."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.core import _own
from app.api.deps import current_user_id, db
from app.config import get_settings
from app.db import system_session
from app.models import (
    Application,
    Campaign,
    Company,
    Escalation,
    MailAccount,
    Message,
    Posting,
    Run,
    UsageDaily,
    User,
)
from app.services import inbox
from app.tasks import scheduler
from app.tasks.locks import get_redis

router = APIRouter(prefix="/api", tags=["replies"])


def _esc_out(session: Session, esc: Escalation) -> dict:
    app = session.get(Application, esc.application_id)
    posting = session.get(Posting, app.posting_id)
    company = session.get(Company, posting.company_id) if posting.company_id else None
    msg = session.get(Message, esc.message_id) if esc.message_id else None
    return {
        "id": esc.id, "application_id": app.id, "company": company.name if company else "", "title": posting.title,
        "reason": esc.reason, "urgency": esc.urgency, "state": esc.state, "suggested_reply": esc.suggested_reply,
        "created_at": esc.created_at,
        "message": {"sender": msg.sender, "subject": msg.subject, "body": msg.body, "received_at": msg.received_at,
                    "category": msg.category, "summary": msg.summary} if msg else None,
    }


@router.get("/escalations")
def list_escalations(state: str = "open", user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db)):
    rows = session.execute(
        select(Escalation).where(Escalation.user_id == user_id, Escalation.state == state)
        .order_by(Escalation.urgency.desc(), Escalation.created_at.desc()).limit(200)
    ).scalars()
    return [_esc_out(session, e) for e in rows]


class AnswerIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


def _resolve(session: Session, esc: Escalation, state: str) -> None:
    esc.state = state
    esc.resolved_at = datetime.now(UTC)
    session.flush()


@router.post("/escalations/{esc_id}/answer")
def answer(esc_id: uuid.UUID, body: AnswerIn, session: Session = Depends(db)):
    """Send the suggested or edited reply. It goes out in the user's name, from the user's mailbox."""
    esc = _own(session, Escalation, esc_id)
    if esc.state != "open":
        raise HTTPException(409, "This item is already handled")
    app = session.get(Application, esc.application_id)
    msg = session.get(Message, esc.message_id) if esc.message_id else None
    try:
        inbox.send_reply(session, app, body.body, actor="user", answering=msg)
    except inbox.ReplyRefused as r:
        raise HTTPException(409, r.message) from r
    if msg:
        msg.handled_by = "user"
    _resolve(session, esc, "answered")
    return _esc_out(session, esc)


@router.post("/escalations/{esc_id}/dismiss")
def dismiss(esc_id: uuid.UUID, replied_myself: bool = False, session: Session = Depends(db)):
    esc = _own(session, Escalation, esc_id)
    app = session.get(Application, esc.application_id)
    if replied_myself:
        app.human_took_over = True
    _resolve(session, esc, "dismissed")
    audit.record(session, user_id=esc.user_id, actor="user", tool="dismiss_escalation", entity_id=app.id,
                 reason="you replied yourself" if replied_myself else "dismissed")
    return _esc_out(session, esc)


@router.post("/inbox/sync")
def sync_now(user_id: uuid.UUID = Depends(current_user_id)):
    """Check for replies now instead of waiting for the schedule."""
    return scheduler.inbox_user(user_id)


@router.post("/mail/google/push")
async def gmail_push(request: Request, token: str = ""):
    """Google Cloud Pub/Sub push endpoint. The payload only says which mailbox changed;
    the inbox run then asks Gmail for the changes (section 6)."""
    expected = get_settings().pubsub_verification_token
    if not expected or token != expected:
        raise HTTPException(403, "Forbidden")
    try:
        envelope = await request.json()
        data = json.loads(base64.b64decode(envelope["message"]["data"]))
        address = str(data["emailAddress"]).lower()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "Malformed push message") from exc
    with system_session() as s:
        account = s.execute(select(MailAccount).where(MailAccount.address == address, MailAccount.status == "connected")).scalars().first()
        user_id = account.user_id if account else None
    if user_id:
        scheduler.inbox_user(user_id)
    return {"ok": True}       # always acknowledge so Pub/Sub does not redeliver


@router.get("/stats/daily")
def daily_stats(days: int = Query(14, ge=1, le=90), user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db)):
    since = datetime.now(UTC) - timedelta(days=days)
    sent = dict(session.execute(
        select(func.date(Application.sent_at), func.count()).where(Application.user_id == user_id, Application.sent_at >= since)
        .group_by(func.date(Application.sent_at))
    ).all())
    replies = dict(session.execute(
        select(func.date(Message.received_at), func.count()).where(Message.user_id == user_id, Message.direction == "in",
                                                                   Message.received_at >= since)
        .group_by(func.date(Message.received_at))
    ).all())
    out = []
    for i in range(days, -1, -1):
        day = (datetime.now(UTC) - timedelta(days=i)).date()
        out.append({"day": day.isoformat(), "sent": int(sent.get(day, 0)), "replies": int(replies.get(day, 0))})
    return out


# ---- Operator console (section 12) ----

def _operator(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db)) -> uuid.UUID:
    user = session.get(User, user_id)
    if not user or not user.is_operator:
        raise HTTPException(404, "Not found")
    return user_id


@router.get("/operator/overview")
def operator_overview(_: uuid.UUID = Depends(_operator)):
    today = datetime.now(UTC).date()
    day_ago = datetime.now(UTC) - timedelta(days=1)
    with system_session() as s:
        users = s.execute(select(User).order_by(User.created_at)).scalars().all()
        spend = dict(s.execute(select(UsageDaily.user_id, UsageDaily.cost_usd).where(UsageDaily.day == today)).all())
        mail = {a.user_id: a.status for a in s.execute(select(MailAccount)).scalars()}
        failed = dict(s.execute(select(Run.user_id, func.count()).where(Run.state == "failed", Run.created_at >= day_ago).group_by(Run.user_id)).all())
        sent = dict(s.execute(select(Application.user_id, func.count()).where(Application.sent_at >= day_ago).group_by(Application.user_id)).all())
        return {
            "global_pause": bool(get_redis().get("global_pause")),
            "users": [{"id": u.id, "email": u.email, "status": u.status, "plan": u.plan, "mail": mail.get(u.id, "none"),
                       "spend_today_usd": round(float(spend.get(u.id, 0.0)), 4), "failed_runs_24h": int(failed.get(u.id, 0)),
                       "sent_24h": int(sent.get(u.id, 0))} for u in users],
        }


@router.post("/operator/users/{target}/status")
def operator_set_status(target: uuid.UUID, status: str, _: uuid.UUID = Depends(_operator)):
    """Kill switch per user: a paused account sends nothing."""
    if status not in ("active", "paused"):
        raise HTTPException(422, "status must be active or paused")
    with system_session() as s:
        user = s.get(User, target)
        if not user:
            raise HTTPException(404, "Not found")
        user.status = status
        if status == "paused":
            for c in s.execute(select(Campaign).where(Campaign.user_id == target)).scalars():
                c.state = "paused"
    return {"id": target, "status": status}


@router.post("/operator/global-pause")
def operator_global_pause(on: bool, _: uuid.UUID = Depends(_operator)):
    """Kill switch for the whole system: the scheduler does nothing while this is on."""
    r = get_redis()
    if on:
        r.set("global_pause", "1")
    else:
        r.delete("global_pause")
    return {"global_pause": on}
