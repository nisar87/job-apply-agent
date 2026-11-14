"""Shapes returned by the API."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CVOut(Out):
    id: uuid.UUID
    label: str
    target_roles: list
    keywords: list
    language: str
    is_default: bool
    active: bool
    current_version_id: uuid.UUID | None
    created_at: datetime


class CampaignOut(Out):
    id: uuid.UUID
    name: str
    state: str
    config: dict
    approval_mode: str
    auto_above_score: int
    created_at: datetime

    @field_validator("config")
    @classmethod
    def _complete(cls, v: dict) -> dict:
        """Every setting with its default filled in, so a search saved before a setting existed shows
        the value the agent really uses."""
        from app.services.campaigns import CampaignConfig

        try:
            return CampaignConfig.model_validate(v or {}).model_dump(mode="json")
        except ValueError:
            return v


class ApplicationOut(Out):
    id: uuid.UUID
    posting_id: uuid.UUID
    campaign_id: uuid.UUID
    status: str
    route: str
    skip_reason: str
    subject: str
    body: str
    language: str
    recipient: str | None
    score: float | None
    check_results: dict
    approved_at: datetime | None
    planned_send_at: datetime | None
    sent_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # joined in by the endpoint
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    cv_label: str = ""
    campaign_name: str = ""


class MatchOut(Out):
    id: uuid.UUID
    posting_id: uuid.UUID
    campaign_id: uuid.UUID
    decision: str
    reason: str
    total_score: float | None
    rubric: dict
    filter_result: dict
    created_at: datetime
    title: str = ""
    company: str = ""
    url: str = ""
    route: str = ""


class NotificationOut(Out):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    link: str | None
    read: bool
    created_at: datetime


class AuditOut(Out):
    id: uuid.UUID
    actor: str
    tool: str
    input: dict
    result: dict
    reason: str
    created_at: datetime


class BlockOut(Out):
    id: uuid.UUID
    type: str
    value: str
    reason: str
    removable: bool


class PostingOut(Out):
    id: uuid.UUID
    title: str
    url: str
    description: str
    fields: dict
    country: str | None
    posted_date: date | None
    first_seen: datetime
    apply_route: str
    contact_email: str | None
