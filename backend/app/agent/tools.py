"""Tools the agent can call (master document, section 4).

Each tool is a thin wrapper over the service layer. Arguments are IDs and short
strings only; every limit is enforced inside the service it calls.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.connectors.sources import matches_keywords
from app.llm.base import LLMClient, SpendCapExceeded, ToolSpec
from app.models import Application, Campaign, Match, Posting, Preference, User
from app.services import applications as app_service
from app.services import cvs as cv_service
from app.services import matching, rules
from app.services.campaigns import get_config
from app.services.notify import notify
from app.services.postings import company_of

log = logging.getLogger(__name__)


@dataclass
class AgentContext:
    session: Session
    llm: LLMClient
    campaign: Campaign
    user: User
    run_id: uuid.UUID | None = None
    now: datetime | None = None
    finished: str | None = None

    def clock(self) -> datetime:
        return self.now or datetime.now(UTC)


def _uuid(value) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError("not a valid id") from exc


def _preferences(ctx: AgentContext) -> list[str]:
    rows = ctx.session.execute(
        select(Preference).where(
            Preference.user_id == ctx.user.id, Preference.active.is_(True),
            (Preference.campaign_id == ctx.campaign.id) | (Preference.campaign_id.is_(None)),
        ).order_by(Preference.created_at.desc())
    ).scalars()
    return [p.text for p in rows]


def get_status(ctx: AgentContext) -> dict:
    config = get_config(ctx.campaign)
    zone = rules.user_zone(ctx.user)
    now = ctx.clock()
    bounds = rules.window_bounds(config.send_window, zone, now)
    sent = rules.sent_today(ctx.session, ctx.user.id, zone, now, ctx.campaign.id)
    waiting = ctx.session.execute(
        select(Application.status).where(Application.campaign_id == ctx.campaign.id,
                                         Application.status.in_(("awaiting_approval", "queued")))
    ).scalars().all()
    return {
        "campaign_state": ctx.campaign.state, "approval_mode": ctx.campaign.approval_mode,
        "sent_today": sent, "quota_remaining": max(0, config.daily_quota - sent),
        "window_open": bool(bounds and bounds[0] <= now < bounds[1]),
        "minutes_left_in_window": int((bounds[1] - now).total_seconds() // 60) if bounds and now < bounds[1] else 0,
        "awaiting_approval": waiting.count("awaiting_approval"), "queued": waiting.count("queued"),
        "match_threshold": config.match_threshold,
    }


def list_candidates(ctx: AgentContext, limit: int = 30) -> dict:
    """Postings not yet evaluated for this search, best pre-ranked first."""
    config = get_config(ctx.campaign)
    seen = select(Match.posting_id).where(Match.campaign_id == ctx.campaign.id)
    q = select(Posting).where(Posting.is_open.is_(True), Posting.id.not_in(seen)).order_by(Posting.first_seen.desc()).limit(400)
    pool = list(ctx.session.execute(q).scalars())
    if config.keywords:
        pool = [p for p in pool if matches_keywords(f"{p.title} {p.description}", config.keywords)] or pool
    # Code-only filters first: postings that fail them are recorded as skipped without a model call
    # and without spending one of the run's steps.
    facts, passing, filtered = cv_service.get_fact_sheet(ctx.session, ctx.user.id), [], 0
    for p in pool:
        skipped = matching.evaluate(ctx.session, ctx.llm, ctx.campaign, config, p, facts, [], use_model=False)
        if skipped is None:
            passing.append(p)
        else:
            filtered += 1
    if filtered:
        audit.record(ctx.session, user_id=ctx.user.id, actor="agent", tool="filter_candidates", run_id=ctx.run_id,
                     entity_id=ctx.campaign.id, result={"filtered_out": filtered, "remaining": len(passing)})
    ranked = matching.prerank(ctx.session, ctx.user.id, passing, limit=max(1, min(int(limit or 30), 30)))
    if config.priority_countries:       # the person's most wanted countries go first; the rest keep their order
        first = set(config.priority_countries)
        ranked = sorted(ranked, key=lambda p: str(p.country or "").lower() not in first)
    out = []
    for p in ranked:
        company = company_of(ctx.session, p)
        out.append({"posting_id": str(p.id), "title": p.title, "company": company.name if company else "",
                    "route": p.apply_route})
    return {"candidates": out, "filtered_out_by_your_settings": filtered}


def score_job(ctx: AgentContext, posting_id: str) -> dict:
    posting = ctx.session.get(Posting, _uuid(posting_id))
    if posting is None:
        return {"error": "unknown posting"}
    match = matching.evaluate(ctx.session, ctx.llm, ctx.campaign, get_config(ctx.campaign), posting,
                              cv_service.get_fact_sheet(ctx.session, ctx.user.id), _preferences(ctx))
    audit.record(ctx.session, user_id=ctx.user.id, actor="agent", tool="score_job", run_id=ctx.run_id, entity_id=match.id,
                 input={"posting": posting.title}, result={"decision": match.decision, "score": match.total_score},
                 reason=match.reason)
    return {"posting_id": str(posting.id), "decision": match.decision, "score": match.total_score, "reason": match.reason,
            "cv": (match.rubric or {}).get("cv_label")}


def draft_application(ctx: AgentContext, posting_id: str) -> dict:
    match = ctx.session.execute(
        select(Match).where(Match.campaign_id == ctx.campaign.id, Match.posting_id == _uuid(posting_id))
    ).scalar_one_or_none()
    if match is None:
        return {"error": "score this posting first"}
    if match.decision not in ("apply", "manual"):
        return {"error": f"this posting was skipped: {match.reason}"}
    app = app_service.create_draft(ctx.session, ctx.llm, ctx.campaign, match, run_id=ctx.run_id)
    auto_queue(ctx.session, ctx.campaign, app)
    return {"application_id": str(app.id), "status": app.status, "problems": (app.check_results or {}).get("problems", [])}


def auto_queue(session: Session, campaign: Campaign, app: Application) -> None:
    """In the automatic approval modes a clean draft goes straight to the send queue."""
    if app.status != "awaiting_approval":
        return
    if campaign.approval_mode == "full_auto" or (
        campaign.approval_mode == "auto_above" and (app.score or 0) >= campaign.auto_above_score
    ):
        app.status = "queued"
        session.flush()


def send_application(ctx: AgentContext, application_id: str) -> dict:
    app = ctx.session.get(Application, _uuid(application_id))
    if app is None:
        return {"error": "unknown application"}
    try:
        app = app_service.send(ctx.session, app, ctx.clock(), run_id=ctx.run_id)
    except rules.RuleViolation as v:
        return {"sent": False, "rule": v.code, "message": v.message, "retry_later": v.retry_later}
    return {"sent": app.status == "sent", "status": app.status}


def ask_user(ctx: AgentContext, question: str) -> dict:
    question = str(question)[:500]
    notify(ctx.session, ctx.user.id, "question", "The agent has a question", question, "/setup")
    audit.record(ctx.session, user_id=ctx.user.id, actor="agent", tool="ask_user", run_id=ctx.run_id, input={"question": question})
    return {"asked": True}


def save_preference(ctx: AgentContext, text: str) -> dict:
    """Stores a standing instruction. It cannot change hard limits: those live in the
    campaign configuration, which only the user can change."""
    text = str(text).strip()[:500]
    if not text:
        return {"error": "empty preference"}
    ctx.session.add(Preference(user_id=ctx.user.id, campaign_id=ctx.campaign.id, text=text, source="correction"))
    ctx.session.flush()
    return {"saved": True}


def finish(ctx: AgentContext, summary: str = "") -> dict:
    ctx.finished = str(summary)[:1000] or "done"
    return {"finished": True}


def _spec(name: str, description: str, props: dict | None = None, required: list[str] | None = None) -> ToolSpec:
    return ToolSpec(name=name, description=description,
                    parameters={"type": "object", "properties": props or {}, "required": required or []})


_ID = {"type": "string", "description": "An id returned by an earlier tool call"}

REGISTRY = {
    "get_status": (get_status, _spec("get_status", "Today's counters: sent, quota left, window, drafts waiting.")),
    "list_candidates": (list_candidates, _spec("list_candidates", "New postings not yet evaluated for this search, best first.",
                                               {"limit": {"type": "integer"}})),
    "score_job": (score_job, _spec("score_job", "Filter and score one posting against the user's CVs.",
                                   {"posting_id": _ID}, ["posting_id"])),
    "draft_application": (draft_application, _spec("draft_application", "Write the application for a posting whose decision is 'apply'.",
                                                   {"posting_id": _ID}, ["posting_id"])),
    "send_application": (send_application, _spec("send_application", "Send a queued application. Rules are checked; a refusal explains why.",
                                                 {"application_id": _ID}, ["application_id"])),
    "ask_user": (ask_user, _spec("ask_user", "Leave one question for the user when an instruction is unclear.",
                                 {"question": {"type": "string"}}, ["question"])),
    "save_preference": (save_preference, _spec("save_preference", "Remember a standing instruction the user gave. Cannot change limits.",
                                               {"text": {"type": "string"}}, ["text"])),
    "finish": (finish, _spec("finish", "End the run with a one-line summary.", {"summary": {"type": "string"}})),
}


def call_tool(ctx: AgentContext, name: str, args: dict) -> dict:
    entry = REGISTRY.get(name)
    if entry is None:
        return {"error": f"unknown tool {name}"}
    fn = entry[0]
    # Each call runs in a savepoint: a failure undoes only this call, never the drafts already written in the
    # run. The spend cap is passed up so the loop stops cleanly; other failures become an error result.
    savepoint = ctx.session.begin_nested()
    try:
        out = fn(ctx, **{k: v for k, v in (args or {}).items()})
        savepoint.commit()
        return out
    except SpendCapExceeded:
        savepoint.rollback()
        raise
    except TypeError as exc:
        savepoint.rollback()
        return {"error": f"bad arguments: {exc}"}
    except ValueError as exc:
        savepoint.rollback()
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - model or network failure inside one tool
        savepoint.rollback()
        log.warning("tool %s failed: %s", name, type(exc).__name__)
        return {"error": f"{name} failed ({type(exc).__name__}); try another posting"}
