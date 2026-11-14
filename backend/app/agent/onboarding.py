"""Setup conversation: turn a free-form instruction into a confirmed campaign
(master document, sections 2 and 4)."""

import logging
import uuid

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.llm.base import LLMClient
from app.models import User
from app.services import applications as app_service
from app.services import cvs as cv_service
from app.services.campaigns import CampaignConfig

log = logging.getLogger(__name__)

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
                "exclude_agencies": {"type": "boolean",
                                     "description": "true when the user wants to skip recruitment agencies or recruiters"},
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
        "evidence": {
            "type": "array",
            "description": "One entry for every setting present in config: the user's own words that state it",
            "items": {
                "type": "object",
                "properties": {
                    "setting": {"type": "string", "description": "The key in config"},
                    "user_words": {"type": "string", "description": "Copied word for word from a user message"},
                },
                "required": ["setting", "user_words"],
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
the user has stated.

Settings: put a key into config only if the user stated it. Leave every other key out entirely; defaults fill \
the rest, and a value the user did not give is an error. For each key you do put into config, add an evidence \
entry quoting the user's words for it, copied exactly. A setting without a matching quote is discarded. \
"Skip agencies" or "no recruiters" means exclude_agencies true. A cooldown stated for companies is \
company_cooldown_days; one stated for contacts or HR addresses is contact_cooldown_days.

Be complete: every country, work mode, limit, time window, cooldown and exclusion the user named must appear in \
config, each with its quote. Countries are ISO codes: Germany is de, Austria is at, Switzerland is ch.

Reply: if an instruction is vague or contradicts another, ask exactly one short question. Otherwise confirm \
what you understood in two or three sentences. Do not say anything about what is still missing on the account \
(CV, mailbox, and so on): the product adds that itself. Never promise something the product cannot do: it \
applies by email from the user's own Gmail, checks replies, and never applies to the same job twice."""

MISSING_TEXT = {
    "cv": "upload a CV", "mail": "connect your Gmail", "escalation_email": "give an address for important replies",
    "fact_sheet": "fill in the fact sheet",
}
_WINDOW_KEYS = {"send_window_start", "send_window_end"}


def _plain(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in str(text)).split())


def stated_settings(patch: dict, evidence: list, history: list[dict]) -> tuple[dict, list[str]]:
    """Keep only the settings the user really stated. The model must quote the user for each one,
    and the quote must be found in what the user wrote; anything else is dropped and named."""
    said = set(_plain(" ".join(str(m.get("text", "")) for m in history if m.get("role") == "user")).split())
    quoted: set[str] = set()
    for item in evidence or []:
        words = _plain(item.get("user_words", "")).split() if isinstance(item, dict) else []
        # Every quoted word must be one the user wrote. Order is not checked, so a quote stitched
        # from two places in the message still counts; a quote with words the user never used does not.
        if words and all(w in said for w in words):
            quoted.add(str(item.get("setting", "")))
    if quoted & (_WINDOW_KEYS | {"send_window"}):
        quoted |= _WINDOW_KEYS
    kept, dropped = {}, []
    for key, value in (patch or {}).items():
        if value in (None, "", []):
            continue
        if key in quoted:
            kept[key] = value
        else:
            dropped.append(key)
    return kept, dropped


_LABELS = {
    "keywords": "roles", "countries": "countries", "locations": "locations", "work_modes": "work mode",
    "contract_types": "contract type", "seniority": "seniority", "languages": "posting languages",
    "salary_min": "minimum yearly salary", "max_posting_age_days": "posted within (days)",
    "exclude_agencies": "skip recruitment agencies", "daily_quota": "applications per day",
    "company_cooldown_days": "days before the same company again",
    "contact_cooldown_days": "days before the same contact again", "match_threshold": "minimum match score",
    "follow_up_enabled": "send a follow-up", "follow_up_after_days": "follow up after (days)",
}


def describe_settings(config: dict) -> str:
    """The settings that differ from the defaults, in plain words. Built from what is actually
    stored, so the confirmation can never claim a setting that was not taken over."""
    defaults = CampaignConfig().model_dump(mode="json")
    parts = []
    for key, label in _LABELS.items():
        value = config.get(key)
        if value == defaults.get(key) or value in (None, "", []):
            continue
        if isinstance(value, bool):
            text = "yes" if value else "no"
        elif isinstance(value, list):
            text = ", ".join(str(v) for v in value)
        else:
            text = f"{value:g}" if isinstance(value, float) else str(value)
        parts.append(f"{label}: {text}")
    window, default_window = config.get("send_window") or {}, defaults["send_window"]
    if (window.get("start"), window.get("end")) != (default_window["start"], default_window["end"]):
        parts.append(f"send between {window.get('start')} and {window.get('end')}")
    return "; ".join(parts)


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
    )
    data = llm.generate_structured(prompt, ONBOARDING_SCHEMA, system=SYSTEM, tier="strong", task="onboarding").data or {}
    patch, dropped = stated_settings(data.get("config") or {}, data.get("evidence") or [], history)
    config, problems = merge_config(draft or {}, patch)
    reply = data.get("reply") or "Tell me what kind of jobs you are looking for."
    keywords_from_cv = False
    if not config.get("keywords"):
        # The user named no roles: search for what their CVs show they can do, and say so.
        cv_terms = cv_service.search_keywords(session, user_id)
        if cv_terms:
            config["keywords"] = cv_terms
            keywords_from_cv = True
            reply += (f" Since you did not name roles, I will look for jobs that match your CV: {', '.join(cv_terms)}."
                      " Tell me if you want something else.")
    if not data.get("open_question", False) and not problems:
        summary = describe_settings(config)
        if summary:
            reply += f" Saved settings: {summary}. Everything else is at its default; tell me if something is missing."
    log.info("setup chat: model gave %s, kept %s, dropped %s", data.get("config"), sorted(patch), dropped)
    if problems:
        reply += " One thing needs fixing: " + " ".join(problems)
    escalation = (data.get("escalation_email") or "").strip()
    if escalation and "@" in escalation and status["escalation_email"] is None:
        user = session.get(User, user_id)
        user.escalation_email = escalation
        session.flush()
        status = setup_status(session, user_id)
    if status["missing"] and not data.get("open_question", False):
        reply += " Before I can start, please " + ", ".join(MISSING_TEXT.get(m, m) for m in status["missing"]) + "."
    return {
        "reply": reply, "config": config, "campaign_name": data.get("campaign_name") or "My job search",
        "preferences": [p for p in (data.get("preferences") or []) if str(p).strip()][:10],
        "not_taken_over": dropped, "keywords_from_cv": keywords_from_cv,
        "status": status, "ready": not status["missing"] and not problems and not data.get("open_question", False)
        and bool(config.get("keywords")),
    }
