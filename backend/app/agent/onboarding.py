"""Setup conversation: turn a free-form instruction into a confirmed campaign
(master document, sections 2 and 4)."""

import uuid

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.llm.base import LLMClient
from app.models import User
from app.services import applications as app_service
from app.services import cvs as cv_service
from app.services.campaigns import CampaignConfig

ONBOARDING_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "What to say to the user next: a short confirmation or ONE question"},
        "campaign_name": {"type": "string"},
        "config": {
            "type": "object",
            "description": "Only the settings the user has stated so far",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}},
                "countries": {"type": "array", "items": {"type": "string"}, "description": "ISO alpha-2, lower case"},
                "locations": {"type": "array", "items": {"type": "string"}},
                "work_modes": {"type": "array", "items": {"type": "string", "enum": ["remote", "hybrid", "onsite"]}},
                "contract_types": {"type": "array", "items": {"type": "string",
                                   "enum": ["full_time", "part_time", "contract", "internship"]}},
                "seniority": {"type": "array", "items": {"type": "string", "enum": ["junior", "mid", "senior", "lead"]}},
                "languages": {"type": "array", "items": {"type": "string"}},
                "salary_min": {"type": "number"},
                "max_posting_age_days": {"type": "integer"},
                "exclude_agencies": {"type": "boolean"},
                "daily_quota": {"type": "integer"},
                "send_window_start": {"type": "string", "description": "HH:MM"},
                "send_window_end": {"type": "string", "description": "HH:MM"},
                "company_cooldown_days": {"type": "integer"},
                "contact_cooldown_days": {"type": "integer"},
                "match_threshold": {"type": "integer"},
                "follow_up_enabled": {"type": "boolean"},
                "follow_up_after_days": {"type": "integer"},
            },
        },
        "preferences": {"type": "array", "items": {"type": "string"},
                        "description": "Standing instructions that are not one of the settings above"},
        "escalation_email": {"type": "string"},
        "open_question": {"type": "boolean", "description": "True if the reply asks the user something"},
    },
    "required": ["reply", "config"],
}

SYSTEM = """You set up a job application agent for one user. Read the conversation and extract the settings \
the user has stated. Do not invent values the user did not give; defaults fill the rest. If an instruction is \
vague or contradicts another, ask exactly one short question. Otherwise confirm what you understood in two or \
three sentences and say what is still missing. Never promise something the product cannot do: it applies by \
email from the user's own Gmail, checks replies, and never applies to the same job twice."""


def setup_status(session: Session, user_id: uuid.UUID) -> dict:
    user = session.get(User, user_id)
    has_cv = bool(cv_service.list_cvs(session, user_id, active_only=True))
    facts = cv_service.get_fact_sheet(session, user_id)
    missing = []
    if not has_cv:
        missing.append("cv")
    if app_service.mail_account(session, user_id) is None:
        missing.append("mail")
    if not user.escalation_email:
        missing.append("escalation_email")
    if not facts:
        missing.append("fact_sheet")
    return {"has_cv": has_cv, "mail_connected": "mail" not in missing, "escalation_email": user.escalation_email,
            "fact_sheet_filled": bool(facts), "missing": missing}


def merge_config(current: dict, patch: dict) -> tuple[dict, list[str]]:
    """Apply the model's patch onto the draft configuration. Invalid values are dropped
    and reported, so a bad extraction can never produce an invalid campaign."""
    merged = dict(current or {})
    window = dict(merged.get("send_window") or {})
    for key, value in (patch or {}).items():
        if value in (None, "", []):
            continue
        if key == "send_window_start":
            window["start"] = value
        elif key == "send_window_end":
            window["end"] = value
        else:
            merged[key] = value
    if window:
        merged["send_window"] = window
    problems: list[str] = []
    try:
        config = CampaignConfig.model_validate(merged)
        return config.model_dump(mode="json"), problems
    except ValidationError as exc:
        for err in exc.errors():
            problems.append(str(err.get("msg", "invalid value")).removeprefix("Value error, "))
        # Fall back to what was valid before this turn.
        try:
            return CampaignConfig.model_validate(current or {}).model_dump(mode="json"), problems
        except ValidationError:
            return CampaignConfig().model_dump(mode="json"), problems


def onboarding_turn(session: Session, llm: LLMClient, user_id: uuid.UUID, history: list[dict], draft: dict | None) -> dict:
    """One turn of the setup chat. `history` is [{role: 'user'|'agent', text}], newest last."""
    convo = "\n".join(f"{'User' if m.get('role') == 'user' else 'Agent'}: {str(m.get('text', ''))[:2000]}" for m in history[-20:])
    status = setup_status(session, user_id)
    prompt = (
        f"Conversation so far:\n{convo}\n\nSettings understood so far: {draft or {}}\n"
        f"Still missing on the account: {status['missing']}\n"
    )
    data = llm.generate_structured(prompt, ONBOARDING_SCHEMA, system=SYSTEM, tier="strong", task="onboarding").data or {}
    config, problems = merge_config(draft or {}, data.get("config") or {})
    reply = data.get("reply") or "Tell me what kind of jobs you are looking for."
    if problems:
        reply += " One thing needs fixing: " + " ".join(problems)
    escalation = (data.get("escalation_email") or "").strip()
    if escalation and "@" in escalation and status["escalation_email"] is None:
        user = session.get(User, user_id)
        user.escalation_email = escalation
        session.flush()
        status = setup_status(session, user_id)
    return {
        "reply": reply, "config": config, "campaign_name": data.get("campaign_name") or "My job search",
        "preferences": [p for p in (data.get("preferences") or []) if str(p).strip()][:10],
        "status": status, "ready": not status["missing"] and not problems and not data.get("open_question", False)
        and bool(config.get("keywords")),
    }
