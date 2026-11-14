"""Data export, account deletion, the public employer opt-out, and operator health."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user_id, db
from app.api.replies import _operator
from app.config import get_settings
from app.db import system_session
from app.models import Application, BlockRequest, Escalation, GlobalBlock, MailAccount, Run, UsageDaily, User
from app.security import verify_password
from app.services import account as account_service
from app.services.postings import domain_of, registered_domain
from app.tasks.locks import get_redis

router = APIRouter(prefix="/api", tags=["account"])


def rate_limit(key: str, limit: int, window_seconds: int) -> None:
    r = get_redis()
    count = r.incr(key)
    if count == 1:
        r.expire(key, window_seconds)
    if count > limit:
        raise HTTPException(429, "Too many attempts. Try again later.")


@router.get("/account/export")
def export(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db)):
    return account_service.export_data(session, user_id)


class DeleteIn(BaseModel):
    password: str


@router.post("/account/delete", status_code=204)
def delete(body: DeleteIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db)):
    user = session.get(User, user_id)
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "Wrong password")
    account_service.delete_account(session, user_id)


class BlockRequestIn(BaseModel):
    contact_email: EmailStr
    company: str = Field(default="", max_length=300)
    message: str = Field(default="", max_length=2000)


@router.post("/public/block-request", status_code=202)
def block_request(body: BlockRequestIn, request: Request):
    """Employers ask not to be contacted. No login. The operator reviews each request
    before it takes effect, so this form cannot be used to block a competitor."""
    rate_limit(f"rl:blockreq:{request.client.host if request.client else 'unknown'}", 5, 3600)
    with system_session() as s:
        s.add(BlockRequest(contact_email=body.contact_email.lower(), company=body.company, message=body.message))
    return {"received": True}


@router.get("/operator/block-requests")
def list_block_requests(_: uuid.UUID = Depends(_operator)):
    with system_session() as s:
        rows = s.execute(select(BlockRequest).where(BlockRequest.state == "open").order_by(BlockRequest.created_at)).scalars()
        return [{"id": r.id, "contact_email": r.contact_email, "company": r.company, "message": r.message,
                 "created_at": r.created_at} for r in rows]


@router.post("/operator/block-requests/{request_id}")
def decide_block_request(request_id: uuid.UUID, approve: bool, whole_domain: bool = True, _: uuid.UUID = Depends(_operator)):
    with system_session() as s:
        req = s.get(BlockRequest, request_id)
        if not req or req.state != "open":
            raise HTTPException(404, "Not found")
        req.state = "approved" if approve else "declined"
        if approve:
            values = [("address", req.contact_email)]
            if whole_domain:
                values.append(("domain", registered_domain(domain_of(req.contact_email))))
            for kind, value in values:
                if not s.execute(select(GlobalBlock).where(GlobalBlock.type == kind, GlobalBlock.value == value)).first():
                    s.add(GlobalBlock(type=kind, value=value, reason="asked not to be contacted"))
        return {"id": req.id, "state": req.state}


@router.get("/operator/health")
def operator_health(_: uuid.UUID = Depends(_operator)):
    """The alert conditions from the master document, section 15, as a list of open problems."""
    now = datetime.now(UTC)
    day_ago = now - timedelta(days=1)
    s_cfg = get_settings()
    alerts: list[dict] = []
    with system_session() as s:
        for a in s.execute(select(MailAccount)).scalars():
            if a.status != "connected":
                alerts.append({"kind": "mail_disconnected", "detail": a.address})
            elif a.provider == "gmail" and a.watch_expiry and a.watch_expiry - now < timedelta(hours=48):
                alerts.append({"kind": "watch_expiring", "detail": a.address})
        failed = int(s.execute(select(func.count()).select_from(Run).where(Run.state == "failed", Run.created_at >= day_ago)).scalar() or 0)
        total = int(s.execute(select(func.count()).select_from(Run).where(Run.created_at >= day_ago)).scalar() or 0)
        if total and failed / total > 0.02:
            alerts.append({"kind": "run_failures", "detail": f"{failed} of {total} runs failed in 24 hours"})
        stopped = int(s.execute(select(func.count()).select_from(Run).where(Run.state == "stopped", Run.created_at >= day_ago)).scalar() or 0)
        if stopped:
            alerts.append({"kind": "runs_hit_limits", "detail": f"{stopped} runs hit a step, time or spend limit"})
        sent = dict(s.execute(select(Application.user_id, func.count()).where(Application.sent_at >= day_ago).group_by(Application.user_id)).all())
        bounced = dict(s.execute(select(Application.user_id, func.count()).where(
            Application.status == "undeliverable", Application.updated_at >= day_ago).group_by(Application.user_id)).all())
        for uid, n in bounced.items():
            if n >= 3 or (sent.get(uid) and n / sent[uid] > 0.05):
                alerts.append({"kind": "bounce_rate", "detail": f"user {uid}: {n} bounces in 24 hours"})
        for row in s.execute(select(UsageDaily).where(UsageDaily.day == now.date(), UsageDaily.cost_usd >= s_cfg.llm_daily_spend_cap_usd)).scalars():
            alerts.append({"kind": "spend_cap_reached", "detail": f"user {row.user_id}: ${row.cost_usd:.2f} today"})
        stale = int(s.execute(select(func.count()).select_from(Escalation).where(
            Escalation.state == "open", Escalation.urgency == "urgent", Escalation.created_at < day_ago)).scalar() or 0)
        if stale:
            alerts.append({"kind": "urgent_unanswered", "detail": f"{stale} urgent replies unanswered for over 24 hours"})
        stuck = int(s.execute(select(func.count()).select_from(Application).where(Application.status == "sending",
                                                                                  Application.updated_at < now - timedelta(minutes=15))).scalar() or 0)
        if stuck:
            alerts.append({"kind": "stuck_sends", "detail": f"{stuck} applications stuck in 'sending'"})
    if get_redis().get("global_pause"):
        alerts.append({"kind": "global_pause", "detail": "sending is paused for everyone"})
    return {"ok": not alerts, "alerts": alerts, "checked_at": now}
