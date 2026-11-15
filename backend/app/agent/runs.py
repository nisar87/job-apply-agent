"""Run types the scheduler starts (master document, section 4): discovery, prepare
(score and draft) and dispatch (plan and send)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.agent.loop import prepare
from app.agent.tools import AgentContext
from app.llm.base import LLMClient, MeteredLLM
from app.models import Application, Campaign, User
from app.runs import finish_run, start_run
from app.services import applications as app_service
from app.services import discovery, rules, slots
from app.services.campaigns import get_config


def discovery_run(session: Session, llm: LLMClient, campaign: Campaign, **kw) -> dict:
    run = start_run(session, campaign.user_id, "discovery", campaign.id)
    metered = MeteredLLM(llm, session, campaign.user_id)
    try:
        result = discovery.discover(session, metered, campaign, run_id=run.id, **kw)
    except Exception as exc:  # noqa: BLE001
        _record_failure(session, run, exc, metered.run_cost)
        raise
    finish_run(session, run, "done", f"found {result['found']}, new {result['new']}", metered.run_cost)
    return result


def _record_failure(session: Session, run, exc: Exception, cost: float) -> None:
    """Keep a failed run on record (and the work done before the failure, when the session is still usable).
    The scheduler looks at the latest run, so a recorded failure waits for the normal interval instead of
    being retried, and paid for again, on every five-minute tick."""
    summary = f"{type(exc).__name__}: {exc}"[:500]
    user_id, run_type, campaign_id = run.user_id, run.type, run.campaign_id
    try:
        finish_run(session, run, "failed", summary, cost)
        session.commit()
    except Exception:  # noqa: BLE001 - the session itself is broken: start clean and record only the failure
        session.rollback()
        failed = start_run(session, user_id, run_type, campaign_id)
        finish_run(session, failed, "failed", summary, cost)
        session.commit()


def prepare_run(session: Session, llm: LLMClient, campaign: Campaign, now: datetime | None = None) -> dict:
    """Score candidates and draft applications for one campaign."""
    user = session.get(User, campaign.user_id)
    run = start_run(session, user.id, "prepare", campaign.id)
    metered = MeteredLLM(llm, session, user.id)
    ctx = AgentContext(session=session, llm=metered, campaign=campaign, user=user, run_id=run.id, now=now)
    try:
        outcome = prepare(ctx)
    except Exception as exc:  # noqa: BLE001
        _record_failure(session, run, exc, metered.run_cost)
        raise
    run.steps = outcome["steps"]
    state = "done" if outcome["stop_reason"] in ("finished", "model stopped") else "stopped"
    finish_run(session, run, state, f"{outcome['summary']} ({outcome['stop_reason']})", metered.run_cost)
    _tell_about_new_drafts(session, campaign, run.created_at)
    return outcome


def _tell_about_new_drafts(session: Session, campaign: Campaign, since: datetime) -> None:
    """One notice per run that left drafts waiting for the user's approval, so they know to look today."""
    rows = session.execute(
        select(Application.status, Application.subject).where(
            Application.campaign_id == campaign.id, Application.status.in_(("awaiting_approval", "manual_apply")),
            Application.created_at >= since)
    ).all()
    drafts = [s for st, s in rows if st == "awaiting_approval"]
    on_site = [s for st, s in rows if st == "manual_apply"]
    if not rows:
        return
    from app.services.notify import notify

    def listed(items):
        return "\n".join(f"- {t}" for t in items[:10]) + (f"\n…and {len(items) - 10} more" if len(items) > 10 else "")

    parts = []
    if drafts:
        parts.append(f"Ready for your approval:\n{listed(drafts)}")
    if on_site:
        parts.append(f"Apply on the employer's site (open them from Drafts):\n{listed(on_site)}")
    if drafts:
        title = (f"{len(drafts)} new drafts are ready for your approval" if len(drafts) > 1
                 else "A new draft is ready for your approval")
        if on_site:
            title += f", plus {len(on_site)} to apply on site"
    else:
        title = f"{len(on_site)} new job{'s' if len(on_site) > 1 else ''} to apply to on the employer's site"
    notify(session, campaign.user_id, "drafts_ready", title, f"Search: {campaign.name}\n\n" + "\n\n".join(parts), "/drafts")


def dispatch_run(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> dict:
    """Plan send times for queued applications and send the ones that are due.

    No model call happens here: sending is plain code under the rules in section 10."""
    now = now or datetime.now(UTC)
    user = session.get(User, user_id)
    zone = rules.user_zone(user)
    app_service.recover_stale_sends(session, user_id, now)
    expired = app_service.expire_drafts(session, user_id, now)
    sent, refused = [], {}
    campaigns = session.execute(select(Campaign).where(Campaign.user_id == user_id, Campaign.state != "paused")).scalars().all()
    for campaign in campaigns:
        config = get_config(campaign)
        bounds = rules.window_bounds(config.send_window, zone, now)
        queued = session.execute(
            select(Application).where(Application.campaign_id == campaign.id, Application.status == "queued")
        ).scalars().all()
        waiting = session.execute(
            select(Application).where(Application.campaign_id == campaign.id, Application.status == "awaiting_approval",
                                      Application.place_day.is_not(None))
        ).scalars().all()
        if not bounds or not (bounds[0] <= now < bounds[1]):
            # Window closed: unused places go back to the pool, and plans are dropped.
            for app in [*queued, *waiting]:
                slots.release_place(session, app)
                app.planned_send_at = None
            continue
        if not queued:
            continue
        # A user's own drafts go out in score order; a late-approved draft competes on score too.
        queued.sort(key=lambda a: (-(a.score or 0.0), a.created_at))
        remaining = max(0, config.daily_quota - rules.sent_today(session, user_id, zone, now, campaign.id))
        plan_for = queued[:remaining]
        needs_plan = any(a.planned_send_at is None or a.planned_send_at >= bounds[1] for a in plan_for) or any(
            a.planned_send_at is not None for a in queued[remaining:]
        )
        if needs_plan:
            # Re-plan the rest of the window whenever the queue changed (for example a late approval).
            times = rules.plan_send_times(len(plan_for), now, bounds[1])
            for app in queued:
                app.planned_send_at = None
            for app, when in zip(plan_for, times, strict=False):
                app.planned_send_at = when
        for app in plan_for:
            if app.planned_send_at is None or app.planned_send_at > now:
                continue
            try:
                app_service.send(session, app, now)
                sent.append(str(app.id))
            except rules.RuleViolation as v:
                refused[str(app.id)] = v.code
                if not v.retry_later and app.status == "queued":
                    slots.release_place(session, app)
                    app.status = "skipped"
                    app.skip_reason = v.message
                    audit.record(session, user_id=user_id, actor="system", tool="send_application", entity_id=app.id,
                                 result={"refused": v.code}, reason=v.message)
                elif v.code == "employer_limit":
                    app.skip_reason = "employer limit reached; queued for the next day"
                if v.code in ("too_soon", "campaign_quota", "user_quota", "mail_auth", "no_mail_account"):
                    break
    # Web forms that were approved (or may be submitted automatically) go out under the same rules.
    from app.services import forms

    ready = session.execute(select(Application).where(Application.user_id == user_id, Application.status == "form_ready")).scalars().all()
    for app in sorted(ready, key=lambda a: -(a.score or 0.0)):
        try:
            forms.submit_form(session, app, now=now)
            sent.append(str(app.id))
        except rules.RuleViolation as v:
            refused[str(app.id)] = v.code
            if v.code in ("too_soon", "campaign_quota", "user_quota", "outside_window", "global_pause"):
                break
        except forms.FormStopped as stop:
            refused[str(app.id)] = f"form: {stop.reason}"
    session.flush()
    return {"sent": sent, "refused": refused, "expired": expired}
