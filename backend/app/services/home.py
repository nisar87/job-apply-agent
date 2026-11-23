"""What the Home screen shows: one answer to "is it working, what has it done, what needs me" (UX blueprint)."""

import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import onboarding
from app.models import Application, Campaign, Escalation, Match, Message, Posting, Run, User
from app.services import applications as app_service
from app.services.postings import company_of

SKIP_BUCKETS = [
    ("needs a language you do not work in", r"requires .*which you do not work in|language"),
    ("not your level", r"\blevel\b|seniority|too junior|too senior"),
    ("not your kind of work", r"role type|not one of your role|role family|\ba .* role, not"),
    ("country or visa does not fit", r"right to work|sponsorship|country|visa|\bregion|not clearly remote|excluded"),
    ("already applied or blocked", r"already handled|blocked|cooldown|never contact"),
    ("too old or closed", r"old|closed|expired"),
    ("not a good enough fit", r"score|fit|match|threshold|must-have|essential"),
]


def skip_reason_bucket(reason: str) -> str:
    low = (reason or "").lower()
    for label, pattern in SKIP_BUCKETS:
        if re.search(pattern, low):
            return label
    return "other reasons"


def _state(session: Session, user: User, setup: dict, campaigns: list[Campaign]) -> dict:
    if user.status != "active":
        return {"state": "stopped", "text": "Your agent is stopped.", "fix": {"text": "Contact support", "href": "/settings"}}
    account = app_service.mail_account(session, user.id)
    if "cv" in setup["missing"]:
        return {"state": "cannot_work", "text": "Your agent cannot start yet.", "fix": {"text": "Add your CV", "href": "/setup"}}
    if account is None:
        return {"state": "cannot_work", "text": "Your agent cannot start yet.", "fix": {"text": "Connect Gmail", "href": "/setup"}}
    if account.status == "error":
        return {"state": "cannot_work", "text": "Gmail stopped accepting the saved sign-in.",
                "fix": {"text": "Reconnect Gmail", "href": "/settings#mail"}}
    live = [c for c in campaigns if c.state in ("active", "dry_run")]
    if not live:
        text = "Your agent is paused." if campaigns else "Your agent has no job search yet."
        return {"state": "paused", "text": text, "fix": {"text": "Start it" if campaigns else "Set it up", "href": "/settings" if campaigns else "/setup"}}
    if all(c.state == "dry_run" for c in live):
        return {"state": "working", "text": "Your agent is working in try-it-first mode: it prepares everything and sends nothing.", "fix": None}
    return {"state": "working", "text": "Your agent is working.", "fix": None}


def build(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    user = session.get(User, user_id)
    setup = onboarding.setup_status(session, user_id)
    campaigns = session.execute(select(Campaign).where(Campaign.user_id == user_id)).scalars().all()
    week = now - timedelta(days=7)
    day = now - timedelta(days=1)

    last_run = session.execute(select(func.max(Run.finished_at)).where(Run.user_id == user_id)).scalar()

    needs: list[dict] = []
    for esc in session.execute(select(Escalation).where(Escalation.user_id == user_id, Escalation.state == "open")
                               .order_by(Escalation.created_at.desc()).limit(10)).scalars():
        app = session.get(Application, esc.application_id)
        posting = session.get(Posting, app.posting_id) if app else None
        company = company_of(session, posting) if posting else None
        needs.append({"kind": "reply", "urgent": esc.urgency == "urgent",
                      "title": f"{company.name if company else 'An employer'}: {esc.reason}"[:200], "href": "/applications"
                      if not app else f"/applications/{app.id}"})
    stuck = session.execute(select(Application).where(
        Application.user_id == user_id, Application.status.in_(("manual_apply", "unconfirmed", "failed", "form_ready")))
        .order_by(Application.updated_at.desc()).limit(10)).scalars().all()
    for app in stuck:
        posting = session.get(Posting, app.posting_id)
        company = company_of(session, posting)
        what = {"manual_apply": app.skip_reason or "needs you to apply on their site",
                "unconfirmed": "no confirmation was shown, please check", "failed": app.skip_reason or "something went wrong",
                "form_ready": "the form is filled, waiting for your check"}[app.status]
        needs.append({"kind": app.status, "urgent": False, "title": f"{company.name if company else posting.title}: {what}"[:200],
                      "href": f"/applications/{app.id}"})

    def count(*conds) -> int:
        return int(session.execute(select(func.count()).select_from(Application).where(Application.user_id == user_id, *conds)).scalar() or 0)

    checked = int(session.execute(select(func.count()).select_from(Match).where(Match.user_id == user_id, Match.created_at >= week)).scalar() or 0)
    replied = int(session.execute(select(func.count(func.distinct(Message.application_id))).where(
        Message.user_id == user_id, Message.direction == "in", Message.received_at >= week,
        Message.category.not_in(("auto_ack", "out_of_office", "bounce")))).scalar() or 0)

    latest: list[dict] = []
    for app in session.execute(select(Application).where(Application.user_id == user_id, Application.sent_at.is_not(None))
                               .order_by(Application.sent_at.desc()).limit(8)).scalars():
        posting = session.get(Posting, app.posting_id)
        latest.append({"at": app.sent_at, "kind": "applied", "text": f"{posting.title}, {(company_of(session, posting).name if company_of(session, posting) else '')}".strip(", "),
                       "note": "confirmed" if app.status != "unconfirmed" else "not confirmed yet", "href": f"/applications/{app.id}"})
    for msg in session.execute(select(Message).where(Message.user_id == user_id, Message.direction == "in")
                               .order_by(Message.received_at.desc()).limit(6)).scalars():
        latest.append({"at": msg.received_at, "kind": "reply", "text": (msg.summary or msg.subject)[:140], "note": "",
                       "href": f"/applications/{msg.application_id}"})
    latest.sort(key=lambda x: x["at"], reverse=True)

    skips = session.execute(select(Match.reason).where(Match.user_id == user_id, Match.decision == "skip", Match.created_at >= day)).scalars().all()
    buckets = Counter(skip_reason_bucket(r) for r in skips)

    def matches(*conds) -> int:
        return int(session.execute(select(func.count()).select_from(Match).where(Match.user_id == user_id, *conds)).scalar() or 0)

    # Real counts only. "Submitted" means the send went out; a try-it-first draft or a failed attempt is not one.
    funnel = {
        "checked": matches(),
        "shortlisted": matches(Match.decision.in_(("apply", "manual"))),
        "pending": count(Application.status.in_(("draft", "queued", "awaiting_approval", "form_ready", "sending"))),
        "submitted": count(Application.sent_at.is_not(None)),
        "needs_help": count(Application.status.in_(("manual_apply", "unconfirmed"))),
        "failed": count(Application.status.in_(("failed", "error"))),
        "replies": int(session.execute(select(func.count(func.distinct(Message.application_id))).where(
            Message.user_id == user_id, Message.direction == "in",
            Message.category.not_in(("auto_ack", "out_of_office", "bounce")))).scalar() or 0),
        "interviews": count(Application.status == "interview"),
        "today_checked": matches(Match.created_at >= day),
        "today_submitted": count(Application.sent_at >= day),
    }
    accounts_ok = bool(app_service.mail_account(session, user_id)) and app_service.mail_account(session, user_id).status != "error"

    return {
        "funnel": funnel,
        "mail_ok": accounts_ok,
        "searches": [{"name": c.name, "state": c.state} for c in campaigns],
        "status": {**_state(session, user, setup, campaigns), "last_checked_at": last_run},
        "try_it_first": bool(campaigns) and all(c.state == "dry_run" for c in campaigns if c.state != "paused") and any(
            c.state == "dry_run" for c in campaigns),
        "needs_you": needs[:12],
        "week": {"applied": count(Application.sent_at >= week), "replies": replied, "interviews": count(
            Application.status == "interview", Application.updated_at >= week), "checked": checked},
        "latest": latest[:10],
        "skipped": {"total": len(skips), "reasons": [{"reason": r, "count": n} for r, n in buckets.most_common()]},
    }
