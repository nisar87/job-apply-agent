"""Drafting, approving and sending applications (master document, sections 8 and 10)."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, storage
from app.config import get_settings
from app.connectors.mail import Attachment, GmailProvider, MailAuthError, OutgoingMail, get_mail_provider
from app.llm.base import LLMClient
from app.models import Application, Block, Campaign, CVVersion, MailAccount, Match, Message, Posting, User
from app.services import cvs as cv_service
from app.services import rules, slots
from app.services.campaigns import get_config
from app.services.notify import notify
from app.services.postings import company_of, domain_of, registered_domain
from app.services.writing import run_checks, write_checked
from app.tasks.locks import get_redis, idempotency_key

STALE_SENDING_MINUTES = 10
APPROVED_VALID_DAYS = 7
MANUAL_VALID_DAYS = 14   # a job to apply to on the employer's site stays on the list this long


def resume_after_reconnect(session: Session, user_id: uuid.UUID) -> list[str]:
    """Searches paused only because Gmail stopped accepting the sign-in go back to how they were."""
    resumed = []
    for campaign in session.execute(select(Campaign).where(Campaign.user_id == user_id, Campaign.state == "paused")).scalars():
        key = f"paused_by_mail:{campaign.id}"
        previous = get_redis().get(key)
        if previous:
            campaign.state = previous
            get_redis().delete(key)
            resumed.append(campaign.name)
    if resumed:
        session.flush()
        audit.record(session, user_id=user_id, actor="system", tool="resume_after_reconnect", input={"searches": resumed})
    return resumed


def write_cover_note(session: Session, llm: LLMClient, app: Application) -> str:
    """The cover note for a form-only application, written the first time it is needed."""
    if app.body:
        return app.body
    posting = session.get(Posting, app.posting_id)
    campaign = session.get(Campaign, app.campaign_id)
    user = session.get(User, app.user_id)
    version = session.get(CVVersion, app.cv_version_id)
    match = session.get(Match, app.match_id) if app.match_id else None
    company = company_of(session, posting)
    draft, checks = write_checked(
        llm, posting=posting, company_name=company.name if company else "", version=version,
        rubric=(match.rubric if match else {}) or {}, fact_sheet=cv_service.get_fact_sheet(session, user.id),
        style_guide=user.style_guide or "", sample_emails=list(user.sample_emails or []),
        user_name=user.name or user.email, config=get_config(campaign), previous_bodies=previous_bodies(session, user.id),
    )
    app.subject, app.body, app.claims = draft.get("subject", app.subject), draft.get("body", ""), draft.get("claims", [])
    app.check_results = checks
    session.flush()
    return app.body


def rewrite_unsent(session: Session, llm: LLMClient, user_id: uuid.UUID) -> int:
    """Drafts written under older email rules and not yet sent are written again under the current ones."""
    rows = session.execute(select(Application).where(
        Application.user_id == user_id, Application.route == "email", Application.status.in_(("awaiting_approval", "queued")),
    )).scalars().all()
    done = 0
    for app in rows:
        if (app.check_results or {}).get("style") == "v2":
            continue
        posting = session.get(Posting, app.posting_id)
        campaign = session.get(Campaign, app.campaign_id)
        user = session.get(User, user_id)
        version = session.get(CVVersion, app.cv_version_id)
        match = session.get(Match, app.match_id) if app.match_id else None
        company = company_of(session, posting)
        draft, checks = write_checked(
            llm, posting=posting, company_name=company.name if company else "", version=version,
            rubric=(match.rubric if match else {}) or {}, fact_sheet=cv_service.get_fact_sheet(session, user_id),
            style_guide=user.style_guide or "", sample_emails=list(user.sample_emails or []),
            user_name=user.name or user.email, config=get_config(campaign), previous_bodies=previous_bodies(session, user_id),
        )
        app.subject, app.body, app.claims = draft.get("subject", app.subject), draft.get("body", app.body), draft.get("claims", [])
        app.check_results = checks
        if not checks["passed"]:
            app.status, app.skip_reason = "failed", "; ".join(checks["problems"])
        session.flush()
        done += 1
    return done


def mark_applied_by_hand(session: Session, app: Application) -> Application:
    """The user applied on the employer's site themselves. It now counts as sent: cooldowns, the employer limit
    and reply tracking cover it, and the agent will not offer it again."""
    if app.status not in ("manual_apply", "form_ready"):
        raise ApplicationError("Only a job on the apply-on-site list can be marked as applied")
    now = datetime.now(UTC)
    app.status, app.sent_at, app.approved_at, app.skip_reason = "sent", now, app.approved_at or now, ""
    app.follow_up_due = None
    session.flush()
    audit.record(session, user_id=app.user_id, actor="user", tool="applied_by_hand", entity_id=app.id)
    return app


def skip_manual(session: Session, app: Application, reason: str = "not for you") -> Application:
    if app.status not in ("manual_apply", "form_ready"):
        raise ApplicationError("Only a job on the apply-on-site list can be skipped here")
    app.status, app.skip_reason = "skipped", reason[:200]
    session.flush()
    audit.record(session, user_id=app.user_id, actor="user", tool="skip_manual", entity_id=app.id, reason=reason[:200])
    return app

log = logging.getLogger(__name__)


class ApplicationError(ValueError):
    pass


def previous_bodies(session: Session, user_id: uuid.UUID) -> list[str]:
    rows = session.execute(
        select(Application.body).where(Application.user_id == user_id, Application.sent_at.is_not(None))
        .order_by(Application.sent_at.desc()).limit(20)
    ).scalars()
    return list(rows)


def create_draft(session: Session, llm: LLMClient, campaign: Campaign, match: Match, *, run_id: uuid.UUID | None = None) -> Application:
    """Write the application for a match that passed scoring. One application per posting, ever."""
    existing = session.execute(
        select(Application).where(Application.user_id == campaign.user_id, Application.posting_id == match.posting_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    if match.decision not in ("apply", "manual"):
        raise ApplicationError("This posting did not pass matching")
    config = get_config(campaign)
    posting = session.get(Posting, match.posting_id)
    user = session.get(User, campaign.user_id)
    version = session.get(CVVersion, match.cv_version_id)
    company = company_of(session, posting)
    company_name = company.name if company else ""
    fact_sheet = cv_service.get_fact_sheet(session, user.id)
    route = "email" if match.decision == "apply" else "form"
    if route == "form":
        # Applied to on the employer's site: no email is written now. A cover note is written only if the user
        # asks the agent to fill the form (see write_cover_note), so unused jobs cost nothing.
        draft, checks = {"subject": posting.title, "body": "", "language": "en", "claims": []}, {"passed": True, "problems": []}
    else:
        draft, checks = write_checked(
            llm, posting=posting, company_name=company_name, version=version, rubric=match.rubric or {},
            fact_sheet=fact_sheet, style_guide=user.style_guide or "", sample_emails=list(user.sample_emails or []),
            user_name=user.name or user.email, config=config, previous_bodies=previous_bodies(session, user.id),
        )
    if route == "form":
        status = "manual_apply"
    elif not checks["passed"]:
        status = "failed"
    else:
        status = "awaiting_approval"
    from app.services import tailoring

    attachment = {"name": version.file_name, "key": version.file_key}
    if status != "failed" and campaign.state != "dry_run":      # "Try it first" never spends a call
        try:
            attachment = tailoring.tailored_attachment(
                session, llm, user=user, posting=posting, match=match, version=version,
                purpose="upload" if route == "form" else "email") or attachment
        except Exception:  # noqa: BLE001 - tailoring must never stop an application
            log.exception("tailoring failed for posting %s", posting.id)
    now = datetime.now(UTC)
    app = Application(
        user_id=user.id, posting_id=posting.id, campaign_id=campaign.id, match_id=match.id, cv_version_id=version.id,
        status=status, route=route, idempotency_key=idempotency_key("apply", user.id, posting.id),
        subject=draft.get("subject", ""), body=draft.get("body", ""), language=draft.get("language", "en"),
        claims=draft.get("claims", []), attachments=[attachment],
        recipient=posting.contact_email, recipient_domain=registered_domain(domain_of(posting.contact_email)) if posting.contact_email else None,
        company_id=posting.company_id, score=match.total_score, neutral_score=match.neutral_score, check_results=checks,
        expires_at=now + timedelta(days=MANUAL_VALID_DAYS if route == "form" else config.draft_expiry_days),
        skip_reason="" if checks["passed"] else "; ".join(checks["problems"]),
    )
    session.add(app)
    session.flush()
    audit.record(session, user_id=user.id, actor="agent", tool="draft_application", run_id=run_id, entity_id=app.id,
                 input={"posting": posting.title, "company": company_name, "cv": (match.rubric or {}).get("cv_label")},
                 result={"status": status, "checks": checks}, reason=match.reason)
    if status == "failed":
        notify(session, user.id, "draft_failed", f"A draft for {company_name} needs your attention",
               "It did not pass the checks after one rewrite: " + "; ".join(checks["problems"]), f"/applications/{app.id}")
    return app


def approve(session: Session, app: Application, *, subject: str | None = None, body: str | None = None, llm: LLMClient | None = None) -> Application:
    if app.status not in ("awaiting_approval", "failed"):
        raise ApplicationError("Only a waiting draft can be approved")
    edited = (subject is not None and subject != app.subject) or (body is not None and body != app.body)
    if edited:
        before = app.body
        app.subject = subject if subject is not None else app.subject
        app.body = body if body is not None else app.body
        # The user is responsible for their own edits, but placeholders and structure are still checked.
        posting = session.get(Posting, app.posting_id)
        campaign = session.get(Campaign, app.campaign_id)
        company = company_of(session, posting)
        version = session.get(CVVersion, app.cv_version_id)
        if llm is not None:
            app.check_results = run_checks(
                llm, {"subject": app.subject, "body": app.body}, posting=posting, company_name=company.name if company else "",
                profile=version.profile, fact_sheet=cv_service.get_fact_sheet(session, app.user_id),
                expected_language=app.language, previous_bodies=[], config=get_config(campaign),
            )
        audit.record(session, user_id=app.user_id, actor="user", tool="edit_draft", entity_id=app.id,
                     input={"before": before[:1500], "after": app.body[:1500]})
        if not (app.check_results or {}).get("passed"):
            app.status = "failed"
            app.skip_reason = "; ".join(app.check_results.get("problems", []))
            session.flush()
            raise ApplicationError("The edited draft still has problems: " + app.skip_reason)
    if app.status == "failed" and not (app.check_results or {}).get("passed"):
        raise ApplicationError("This draft has problems. Edit it to fix them, then approve.")
    app.status = "queued"
    app.approved_at = datetime.now(UTC)
    app.skip_reason = ""
    # Approval keeps the draft valid long enough to reach a send window: one approved on Friday evening must
    # still go out on Monday, not expire over the weekend.
    if app.expires_at is not None:
        app.expires_at = max(app.expires_at, app.approved_at + timedelta(days=APPROVED_VALID_DAYS))
    session.flush()
    audit.record(session, user_id=app.user_id, actor="user", tool="approve_draft", entity_id=app.id)
    return app


def reject(session: Session, app: Application, reason: str = "", never_company: bool = False) -> Application:
    if app.status not in ("awaiting_approval", "queued", "failed", "manual_apply"):
        raise ApplicationError("This application can no longer be rejected")
    slots.release_place(session, app)
    app.status = "skipped"
    app.skip_reason = reason or "rejected by you"
    if never_company and app.company_id:
        posting = session.get(Posting, app.posting_id)
        company = company_of(session, posting)
        if company and not session.execute(
            select(Block).where(Block.user_id == app.user_id, Block.type == "company", Block.value == company.name)
        ).first():
            session.add(Block(user_id=app.user_id, type="company", value=company.name, reason="never this company"))
    session.flush()
    audit.record(session, user_id=app.user_id, actor="user", tool="reject_draft", entity_id=app.id, reason=app.skip_reason)
    return app


def expire_drafts(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    rows = session.execute(
        select(Application).where(
            Application.user_id == user_id,
            Application.status.in_(("awaiting_approval", "queued", "failed", "manual_apply")),
            Application.sent_at.is_(None), Application.expires_at < now,
        )
    ).scalars().all()
    for app in rows:
        slots.release_place(session, app)
        manual = app.status == "manual_apply"
        app.status = "skipped"
        app.skip_reason = ("not applied to on the employer's site in time" if manual else
                           "not approved in time" if app.approved_at is None else "could not be sent before it expired")
        audit.record(session, user_id=user_id, actor="system", tool="expire_draft", entity_id=app.id, reason=app.skip_reason)
    session.flush()
    return len(rows)


def mail_account(session: Session, user_id: uuid.UUID) -> MailAccount | None:
    return session.execute(
        select(MailAccount).where(MailAccount.user_id == user_id, MailAccount.status == "connected")
    ).scalars().first()


def _recipient_for_environment(real: str, user_id: uuid.UUID | None = None) -> str:
    """Outside production every application goes to the sandbox address, except for users the admin switched
    to real sending. With the real Gmail provider and no sandbox address configured, sending is refused."""
    from app.services import live

    s = get_settings()
    if s.is_production or live.sends_for_real(user_id):
        return real
    if s.sandbox_recipient:
        return s.sandbox_recipient
    if isinstance(get_mail_provider(), GmailProvider):
        raise rules.RuleViolation("sandbox_required", "Set SANDBOX_RECIPIENT before sending outside production")
    return real


def recover_stale_sends(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> int:
    """An application stuck in 'sending' means the process died mid-send. It is never
    re-sent automatically, because the email may already have left."""
    now = now or datetime.now(UTC)
    rows = session.execute(
        select(Application).where(Application.user_id == user_id, Application.status == "sending",
                                  Application.updated_at < now - timedelta(minutes=STALE_SENDING_MINUTES))
    ).scalars().all()
    for app in rows:
        app.status = "failed"
        app.skip_reason = "The send was interrupted and its outcome is unknown. Check your Sent folder before retrying."
        notify(session, user_id, "send_unknown", "One application may or may not have been sent", app.skip_reason,
               f"/applications/{app.id}")
    session.flush()
    return len(rows)


def send(session: Session, app: Application, now: datetime | None = None, *, run_id: uuid.UUID | None = None) -> Application:
    """Send one application, at most once. Raises RuleViolation when a rule forbids it."""
    now = now or datetime.now(UTC)
    if app.status != "queued":
        raise rules.RuleViolation("not_queued", f"Application is {app.status}, not queued")
    if get_redis().get("global_pause"):
        raise rules.RuleViolation("global_pause", "Sending is paused for the whole system", retry_later=True)
    campaign = session.get(Campaign, app.campaign_id)
    config = get_config(campaign)
    user = session.get(User, app.user_id)
    posting = session.get(Posting, app.posting_id)
    rules.check_send(session, app, campaign, config, user, posting, now)
    zone = rules.user_zone(user)
    if campaign.state == "dry_run":
        slots.release_place(session, app)
        audit.record(session, user_id=user.id, actor="agent", tool="send_application", run_id=run_id, entity_id=app.id,
                     result={"dry_run": True}, reason="dry run: nothing was sent")
        app.status = "dry_run"
        session.flush()
        return app
    account = mail_account(session, user.id)
    if account is None:
        raise rules.RuleViolation("no_mail_account", "Connect your Gmail account first", retry_later=True)
    recipient = _recipient_for_environment(app.recipient, user.id)
    attachments = [Attachment(name=a["name"], data=storage.load(a["key"])) for a in (app.attachments or [])]
    # Mark as sending and commit before the provider call: a crash after this point can
    # never lead to a second send.
    app.status = "sending"
    session.commit()
    try:
        sent = get_mail_provider().send(
            account, OutgoingMail(to=recipient, subject=app.subject, body=app.body, from_name=user.name, attachments=attachments)
        )
    except MailAuthError:
        app.status = "queued"
        account.status = "error"
        if campaign.state != "paused":
            # Remembered so reconnecting Gmail restores the search exactly as it was (see resume_after_reconnect).
            get_redis().set(f"paused_by_mail:{campaign.id}", campaign.state)
        campaign.state = "paused"
        slots.release_place(session, app)
        notify(session, user.id, "reconnect_mail", "Reconnect your Gmail account",
               "Gmail no longer accepts the saved authorization. Your searches are paused until you reconnect.", "/account")
        session.commit()
        raise rules.RuleViolation("mail_auth", "Gmail authorization failed", retry_later=True) from None
    except Exception as exc:  # noqa: BLE001
        app.status = "failed"
        app.skip_reason = f"Sending failed: {type(exc).__name__}. Check your Sent folder before retrying."
        slots.release_place(session, app)
        session.commit()
        raise rules.RuleViolation("send_failed", app.skip_reason) from exc
    app.status = "sent"
    app.sent_at = now
    app.mail_message_id, app.thread_id, app.rfc_message_id = sent.message_id, sent.thread_id, sent.rfc_message_id
    if config.follow_up_enabled:
        app.follow_up_due = now + timedelta(days=config.follow_up_after_days)
    session.add(Message(user_id=user.id, application_id=app.id, direction="out", mail_message_id=sent.message_id,
                        sender=account.address, subject=app.subject, body=app.body, received_at=now, handled_by="agent"))
    slots.mark_sent(session, app, now.astimezone(zone).date())
    audit.record(session, user_id=user.id, actor="agent", tool="send_application", run_id=run_id, entity_id=app.id,
                 input={"to": app.recipient, "delivered_to": recipient, "subject": app.subject},
                 result={"message_id": sent.message_id, "thread_id": sent.thread_id},
                 reason=f"score {app.score}; approved {'by you' if app.approved_at else 'automatically'}")
    session.flush()
    return app


def override_and_draft(session: Session, llm: LLMClient, match: Match) -> Application:
    """'Apply anyway' on a skipped posting: overrides the threshold once (section 12).

    A posting that takes applications only on the employer's site goes on the apply-by-hand list instead."""
    posting = session.get(Posting, match.posting_id)
    if match.cv_version_id is None:
        raise ApplicationError("This posting was filtered out before scoring, so there is nothing to send")
    campaign = session.get(Campaign, match.campaign_id)
    match.decision = "apply" if posting.apply_route == "email" else "manual"
    match.reason = f"you chose to apply anyway (score {match.total_score})"
    app = create_draft(session, llm, campaign, match)
    app.override_threshold = True
    session.flush()
    return app
