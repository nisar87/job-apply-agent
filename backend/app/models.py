"""Data model (master document, section 11)."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)


def user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    locale: Mapped[str] = mapped_column(String(16), default="en")
    plan: Mapped[str] = mapped_column(String(32), default="beta")
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | paused | deleted
    is_operator: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_send_limit: Mapped[int] = mapped_column(Integer, default=20)
    escalation_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    style_guide: Mapped[str] = mapped_column(Text, default="")
    sample_emails: Mapped[list] = mapped_column(JsonType, default=list)


class MailAccount(Base, TimestampMixin):
    __tablename__ = "mail_accounts"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    provider: Mapped[str] = mapped_column(String(32), default="gmail")
    address: Mapped[str] = mapped_column(String(320))
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[list] = mapped_column(JsonType, default=list)
    watch_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="connected")  # connected | disconnected | error
    __table_args__ = (UniqueConstraint("user_id", "address"),)


class CV(Base, TimestampMixin):
    __tablename__ = "cvs"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    label: Mapped[str] = mapped_column(String(100))
    target_roles: Mapped[list] = mapped_column(JsonType, default=list)
    keywords: Mapped[list] = mapped_column(JsonType, default=list)
    language: Mapped[str] = mapped_column(String(16), default="en")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class CVVersion(Base, TimestampMixin):
    __tablename__ = "cv_versions"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    cv_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("cvs.id", ondelete="CASCADE"))
    file_key: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(255), default="cv.pdf")
    file_hash: Mapped[str] = mapped_column(String(64))
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    profile: Mapped[dict] = mapped_column(JsonType, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)


class FactSheet(Base, TimestampMixin):
    __tablename__ = "fact_sheets"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    data: Mapped[dict] = mapped_column(JsonType, default=dict)


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(16), default="paused")  # active | paused | dry_run
    config: Mapped[dict] = mapped_column(JsonType, default=dict)
    approval_mode: Mapped[str] = mapped_column(String(16), default="review")  # review | auto_above | full_auto
    auto_above_score: Mapped[int] = mapped_column(Integer, default=92)


class Preference(Base, TimestampMixin):
    __tablename__ = "preferences"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="user")  # user | correction
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    hiring_system: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hiring_slug: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_agency: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("normalized_name", "domain"),)


class Posting(Base, TimestampMixin):
    __tablename__ = "postings"
    id: Mapped[uuid.UUID] = pk()
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[dict] = mapped_column(JsonType, default=dict)  # extracted fields
    country: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    posted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    apply_route: Mapped[str] = mapped_column(String(16), default="unknown")  # email | form | unknown
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)


class Match(Base, TimestampMixin):
    __tablename__ = "matches"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    posting_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("postings.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    cv_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    filter_result: Mapped[dict] = mapped_column(JsonType, default=dict)
    rubric: Mapped[dict] = mapped_column(JsonType, default=dict)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    neutral_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str] = mapped_column(String(24), default="pending")  # pending | apply | skip | manual
    reason: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("campaign_id", "posting_id"),)


class Application(Base, TimestampMixin):
    __tablename__ = "applications"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    posting_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("postings.id"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"))
    match_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    cv_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    skip_reason: Mapped[str] = mapped_column(Text, default="")
    route: Mapped[str] = mapped_column(String(16), default="email")  # email | form
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(16), default="en")
    claims: Mapped[list] = mapped_column(JsonType, default=list)
    attachments: Mapped[list] = mapped_column(JsonType, default=list)
    recipient: Mapped[str | None] = mapped_column(String(320), nullable=True)
    recipient_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mail_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    follow_up_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    follow_ups_sent: Mapped[int] = mapped_column(Integer, default=0)
    agent_replies: Mapped[int] = mapped_column(Integer, default=0)
    human_took_over: Mapped[bool] = mapped_column(Boolean, default=False)
    form_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    check_results: Mapped[dict] = mapped_column(JsonType, default=dict)
    override_threshold: Mapped[bool] = mapped_column(Boolean, default=False)
    place_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    neutral_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rfc_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    __table_args__ = (
        UniqueConstraint("user_id", "posting_id"),
        Index("ix_app_user_domain_sent", "user_id", "recipient_domain", "sent_at"),
        Index("ix_app_user_company_sent", "user_id", "company_id", "sent_at"),
    )


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    application_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(8))  # in | out
    mail_message_id: Mapped[str] = mapped_column(String(255))
    sender: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    handled_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # agent | user | none
    __table_args__ = (UniqueConstraint("user_id", "mail_message_id"),)


class Escalation(Base, TimestampMixin):
    __tablename__ = "escalations"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    application_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    urgency: Mapped[str] = mapped_column(String(16), default="normal")  # normal | urgent
    suggested_reply: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(16), default="open")  # open | answered | dismissed
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Block(Base, TimestampMixin):
    __tablename__ = "blocks"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    type: Mapped[str] = mapped_column(String(16))  # company | domain | address
    value: Mapped[str] = mapped_column(String(320))
    reason: Mapped[str] = mapped_column(Text, default="")
    removable: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("user_id", "type", "value"),)


class GlobalBlock(Base, TimestampMixin):
    __tablename__ = "global_blocks"
    id: Mapped[uuid.UUID] = pk()
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(320))
    reason: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("type", "value"),)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(16))  # agent | user | system
    tool: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JsonType, default=dict)
    result: Mapped[dict] = mapped_column(JsonType, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="running")  # running | done | failed | stopped
    steps: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    emailed: Mapped[bool] = mapped_column(Boolean, default=False)


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    day: Mapped[date] = mapped_column(Date)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("user_id", "day"),)


class PostingSlot(Base):
    """Places taken per posting per day, shared across users. Holds no user content
    (master document, section 10). Never exposed through a user-facing endpoint."""

    __tablename__ = "posting_slots"
    id: Mapped[uuid.UUID] = pk()
    posting_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("postings.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    held: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("posting_id", "day"),)


# Tables protected by row-level security on user_id.
USER_TABLES = [
    "mail_accounts", "cvs", "cv_versions", "fact_sheets", "campaigns", "preferences", "matches",
    "applications", "messages", "escalations", "blocks", "audit_log", "runs", "notifications",
    "usage_daily",
]
# Shared tables: readable by the app role, no per-user rows.
SHARED_TABLES = ["companies", "postings", "global_blocks", "posting_slots"]
