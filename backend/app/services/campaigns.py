"""Campaigns: one job search with its rules (master document, sections 2 and 10)."""

import uuid
from datetime import time

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.countries import normalize as normalize_country
from app.models import Campaign

DEFAULT_WEIGHTS = {
    "must_have_skills": 40,
    "experience": 20,
    "domain": 15,
    "nice_to_have": 10,
    "language": 10,
    "preferences": 5,
}


class SendWindow(BaseModel):
    start: str = "09:00"
    end: str = "12:00"
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # Monday = 0

    @field_validator("start", "end")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        time.fromisoformat(v)
        return v

    def minutes(self) -> int:
        s, e = time.fromisoformat(self.start), time.fromisoformat(self.end)
        return (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute)

    @model_validator(mode="after")
    def _ordered(self):
        if self.minutes() <= 0:
            raise ValueError("The send window must end after it starts")
        if not self.days or any(d < 0 or d > 6 for d in self.days):
            raise ValueError("Choose at least one valid weekday")
        return self


class CampaignConfig(BaseModel):
    # What to look for
    keywords: list[str] = Field(default_factory=list)
    # Countries to apply within; empty means anywhere. A posting outside them is skipped, unless it is
    # remote and remote_outside_countries allows remote jobs based elsewhere.
    countries: list[str] = Field(default_factory=lambda: ["at"])
    remote_outside_countries: bool = True
    locations: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)  # remote | hybrid | onsite; empty = any
    contract_types: list[str] = Field(default_factory=list)  # full_time | part_time | contract | internship
    seniority: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)  # posting languages the user accepts; empty = any
    salary_min: float | None = None  # per year, in the posting's currency
    max_posting_age_days: int | None = Field(default=7, ge=1, le=60)
    exclude_agencies: bool = False
    sources: list[str] = Field(default_factory=lambda: [
        "adzuna", "arbeitnow", "remotive", "jobicy", "himalayas", "weworkremotely", "remoteok", "workingnomads",
        "greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "personio", "teamtailor",
    ])
    # What to do when a posting does not state something: apply | skip
    unknown_policy: dict[str, str] = Field(
        default_factory=lambda: {"salary": "apply", "work_mode": "apply", "age": "apply"}
    )
    # Limits
    daily_quota: int = Field(default=10, ge=1, le=30)
    send_window: SendWindow = Field(default_factory=SendWindow)
    company_cooldown_days: int = Field(default=15, ge=0, le=365)
    contact_cooldown_days: int = Field(default=15, ge=0, le=365)
    match_threshold: int = Field(default=85, ge=50, le=100)
    weights: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    draft_expiry_days: int = Field(default=3, ge=1, le=7)
    # Replies and follow-up (phase 2)
    follow_up_enabled: bool = False
    follow_up_after_days: int = Field(default=7, ge=2, le=30)
    max_agent_replies: int = Field(default=3, ge=0, le=5)
    answer_routine_questions: bool = True
    thank_you_on_rejection: bool = False
    may_state_salary: bool = False
    # Writing
    email_min_words: int = Field(default=120, ge=40, le=400)
    email_max_words: int = Field(default=180, ge=60, le=600)

    @field_validator("countries")
    @classmethod
    def _known_countries(cls, v: list[str]) -> list[str]:
        out, unknown = [], []
        for item in v:
            code = normalize_country(item)
            if code is None:
                unknown.append(str(item))
            elif code not in out:
                out.append(code)
        if unknown:
            raise ValueError(f"Unknown country: {', '.join(unknown)}. Use a country name or its two-letter code.")
        return out

    @model_validator(mode="after")
    def _consistent(self):
        s = get_settings()
        needed = self.daily_quota * s.avg_send_gap_minutes
        if needed > self.send_window.minutes():
            raise ValueError(
                f"{self.daily_quota} applications a day need about {needed} minutes, but the send window is "
                f"{self.send_window.minutes()} minutes. Widen the window or lower the quota."
            )
        if set(self.weights) != set(DEFAULT_WEIGHTS) or sum(self.weights.values()) != 100:
            raise ValueError("Weights must cover every rubric dimension and add up to 100")
        if self.email_min_words > self.email_max_words:
            raise ValueError("Minimum email length is above the maximum")
        for key, value in self.unknown_policy.items():
            if value not in ("apply", "skip"):
                raise ValueError(f"unknown_policy.{key} must be 'apply' or 'skip'")
        return self


def get_config(campaign: Campaign) -> CampaignConfig:
    return CampaignConfig.model_validate(campaign.config or {})


def list_campaigns(session: Session, user_id: uuid.UUID) -> list[Campaign]:
    return list(session.execute(select(Campaign).where(Campaign.user_id == user_id).order_by(Campaign.created_at)).scalars())


def _check_user_total(session: Session, user_id: uuid.UUID, config: CampaignConfig, exclude: uuid.UUID | None) -> None:
    ceiling = get_settings().user_daily_send_ceiling
    total = config.daily_quota
    for c in list_campaigns(session, user_id):
        if c.id != exclude and c.state != "paused":
            total += get_config(c).daily_quota
    if total > ceiling:
        raise ValueError(f"All active searches together would send {total} a day; the limit per user is {ceiling}.")


def create_campaign(
    session: Session, user_id: uuid.UUID, name: str, config: CampaignConfig, *, actor: str = "user",
    approval_mode: str = "review", state: str = "paused",
) -> Campaign:
    _check_user_total(session, user_id, config, None)
    keywords_from_cv = False
    if not config.keywords:
        # No roles given: search for what the user's CVs show they can do.
        from app.services.cvs import search_keywords

        config = config.model_copy(update={"keywords": search_keywords(session, user_id)})
        keywords_from_cv = bool(config.keywords)
    campaign = Campaign(
        user_id=user_id, name=name, config=config.model_dump(mode="json"), approval_mode=approval_mode, state=state
    )
    session.add(campaign)
    session.flush()
    audit.record(session, user_id=user_id, actor=actor, tool="create_campaign", entity_id=campaign.id,
                 input={"name": name}, result={"state": state, "keywords_from_cv": keywords_from_cv})
    return campaign


def update_campaign(
    session: Session, campaign: Campaign, *, name: str | None = None, config: CampaignConfig | None = None,
    state: str | None = None, approval_mode: str | None = None, auto_above_score: int | None = None,
    actor: str = "user",
) -> Campaign:
    """Hard limits change only through this function, and only with actor 'user'
    (master document, section 10: the agent may suggest, the user confirms)."""
    if actor != "user":
        raise PermissionError("Only the user can change a search's limits")
    changes: dict = {}
    if config is not None:
        _check_user_total(session, campaign.user_id, config, campaign.id)
        campaign.config = config.model_dump(mode="json")
        changes["config"] = True
    if name is not None:
        campaign.name = name
        changes["name"] = name
    if state is not None:
        if state not in ("active", "paused", "dry_run"):
            raise ValueError("Unknown state")
        campaign.state = state
        changes["state"] = state
    if approval_mode is not None:
        if approval_mode not in ("review", "auto_above", "full_auto"):
            raise ValueError("Unknown approval mode")
        campaign.approval_mode = approval_mode
        changes["approval_mode"] = approval_mode
    if auto_above_score is not None:
        campaign.auto_above_score = max(50, min(100, auto_above_score))
        changes["auto_above_score"] = campaign.auto_above_score
    session.flush()
    audit.record(session, user_id=campaign.user_id, actor=actor, tool="update_campaign", entity_id=campaign.id, input=changes)
    return campaign
