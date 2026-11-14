"""Account-level operations: notification delivery, data export and deletion
(master document, sections 9 and 13)."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.config import get_settings
from app.connectors.notify_email import get_notifier
from app.models import (
    CV,
    Application,
    AuditLog,
    Block,
    Campaign,
    CVVersion,
    Escalation,
    FactSheet,
    MailAccount,
    Match,
    Message,
    Notification,
    Preference,
    User,
)
from app.security import decrypt_secret

log = logging.getLogger(__name__)
URGENT_KINDS = {"escalation_urgent", "reconnect_mail", "send_unknown"}
EMAIL_KINDS = URGENT_KINDS | {"escalation", "bounce", "draft_failed", "digest", "question"}


def deliver_notifications(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> int:
    """Email pending notices to the user's escalation address. Urgent ones go at once;
    the rest are batched into at most one email per hour (section 9)."""
    now = now or datetime.now(UTC)
    user = session.get(User, user_id)
    to = user.escalation_email or user.email
    pending = session.execute(
        select(Notification).where(Notification.user_id == user_id, Notification.emailed.is_(False),
                                   Notification.kind.in_(EMAIL_KINDS)).order_by(Notification.created_at)
    ).scalars().all()
    if not pending:
        return 0
    urgent = [n for n in pending if n.kind in URGENT_KINDS]
    normal = [n for n in pending if n.kind not in URGENT_KINDS]
    last_batch = session.execute(
        select(Notification.updated_at).where(Notification.user_id == user_id, Notification.emailed.is_(True),
                                              Notification.kind.not_in(URGENT_KINDS)).order_by(Notification.updated_at.desc())
    ).scalars().first()
    batch_due = last_batch is None or now - last_batch >= timedelta(hours=1)
    to_send = urgent + (normal if batch_due else [])
    if not to_send:
        return 0
    base = get_settings().frontend_url
    subject = to_send[0].title if len(to_send) == 1 else f"{len(to_send)} updates on your job applications"
    body = "\n\n".join(f"{n.title}\n{n.body}".strip() + (f"\n{base}{n.link}" if n.link else "") for n in to_send)
    if not get_notifier().send(to, subject, body):
        return 0
    for n in to_send:
        n.emailed = True
        n.updated_at = now
    session.flush()
    return len(to_send)


def export_data(session: Session, user_id: uuid.UUID) -> dict:
    """Everything stored about the user, as plain data. Tokens are never included."""

    def rows(model, *cols):
        out = []
        for r in session.execute(select(model).where(model.user_id == user_id)).scalars():
            out.append({c: _plain(getattr(r, c)) for c in cols})
        return out

    user = session.get(User, user_id)
    facts = session.execute(select(FactSheet).where(FactSheet.user_id == user_id)).scalar_one_or_none()
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "account": {c: _plain(getattr(user, c)) for c in ("email", "name", "timezone", "locale", "plan", "escalation_email",
                                                          "style_guide", "sample_emails", "created_at")},
        "fact_sheet": facts.data if facts else {},
        "mail_accounts": rows(MailAccount, "address", "provider", "status", "created_at"),
        "cvs": rows(CV, "label", "target_roles", "keywords", "language", "is_default", "active"),
        "cv_versions": rows(CVVersion, "cv_id", "file_name", "file_hash", "profile", "created_at"),
        "campaigns": rows(Campaign, "name", "state", "config", "approval_mode", "created_at"),
        "preferences": rows(Preference, "text", "source", "active", "created_at"),
        "matches": rows(Match, "posting_id", "decision", "reason", "total_score", "created_at"),
        "applications": rows(Application, "posting_id", "status", "route", "subject", "body", "recipient", "score", "sent_at",
                             "skip_reason", "created_at"),
        "messages": rows(Message, "application_id", "direction", "sender", "subject", "body", "category", "received_at"),
        "escalations": rows(Escalation, "application_id", "reason", "urgency", "state", "created_at"),
        "blocks": rows(Block, "type", "value", "reason"),
        "activity_log": rows(AuditLog, "actor", "tool", "reason", "created_at"),
    }


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def delete_account(session: Session, user_id: uuid.UUID) -> None:
    """Remove the user and everything of theirs: revoke Google access, delete stored
    files, then delete the rows (every user table cascades from `users`)."""
    for account in session.execute(select(MailAccount).where(MailAccount.user_id == user_id)).scalars():
        if account.provider == "gmail" and account.encrypted_refresh_token:
            try:
                httpx.post("https://oauth2.googleapis.com/revoke",
                           params={"token": decrypt_secret(account.encrypted_refresh_token)}, timeout=15)
            except Exception:  # noqa: BLE001 - deletion continues even if Google cannot be reached
                log.warning("could not revoke Google access for account %s", account.id)
    storage.delete_user_files(user_id)
    session.delete(session.get(User, user_id))
    session.flush()
