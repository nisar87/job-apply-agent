"""Hard limits, enforced in code at the moment of the action (master document, section 10).

The model is told the limits so it can plan, but it never enforces them: every send
passes through `check_send`.
"""

import random
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Application, Campaign, Posting, User
from app.services import slots
from app.services.campaigns import CampaignConfig, SendWindow
from app.services.matching import blocked_reason
from app.services.postings import domain_of, posting_age_days, registered_domain

SENT_STATES = ("sent", "acknowledged", "in_conversation", "interview", "offer", "rejected", "closed",
               "no_response", "undeliverable")


class RuleViolation(Exception):
    def __init__(self, code: str, message: str, retry_later: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_later = retry_later  # true when waiting may fix it (window, quota, place)


def user_zone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def window_bounds(window: SendWindow, zone: ZoneInfo, now: datetime) -> tuple[datetime, datetime] | None:
    """Today's send window in UTC, or None if today is not a sending day."""
    local = now.astimezone(zone)
    if local.weekday() not in window.days:
        return None
    start = datetime.combine(local.date(), time.fromisoformat(window.start), zone)
    end = datetime.combine(local.date(), time.fromisoformat(window.end), zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def in_window(window: SendWindow, zone: ZoneInfo, now: datetime) -> bool:
    bounds = window_bounds(window, zone, now)
    return bool(bounds and bounds[0] <= now < bounds[1])


def day_start_utc(zone: ZoneInfo, now: datetime) -> datetime:
    local = now.astimezone(zone)
    return datetime.combine(local.date(), time.min, zone).astimezone(UTC)


def sent_today(session: Session, user_id: uuid.UUID, zone: ZoneInfo, now: datetime, campaign_id: uuid.UUID | None = None) -> int:
    q = select(func.count()).select_from(Application).where(
        Application.user_id == user_id, Application.sent_at >= day_start_utc(zone, now)
    )
    if campaign_id:
        q = q.where(Application.campaign_id == campaign_id)
    return int(session.execute(q).scalar() or 0)


def check_send(
    session: Session, app: Application, campaign: Campaign, config: CampaignConfig, user: User, posting: Posting,
    now: datetime | None = None,
) -> None:
    """Raise RuleViolation unless this application may be sent right now."""
    s = get_settings()
    now = now or datetime.now(UTC)
    zone = user_zone(user)
    if user.status != "active":
        raise RuleViolation("user_paused", "The account is paused")
    if campaign.state == "paused":
        raise RuleViolation("campaign_paused", "This search is paused", retry_later=True)
    is_form = app.route == "form"
    if not is_form:
        if not posting.contact_email:
            raise RuleViolation("no_email_route", "This posting has no application email address")
        # The recipient is never free text: it must be the validated address stored on the posting.
        if (app.recipient or "").lower() != posting.contact_email.lower():
            raise RuleViolation("recipient_mismatch", "The recipient does not match the posting's contact address")
    if not (app.check_results or {}).get("passed"):
        raise RuleViolation("checks_failed", "The draft has not passed its checks")
    if app.score is not None and app.score < config.match_threshold and not app.override_threshold:
        raise RuleViolation("below_threshold", f"Score {app.score} is below the threshold of {config.match_threshold}")
    # Forms have their own approval rule (clean reviews per hiring system), checked by the form service.
    if not is_form:
        if campaign.approval_mode == "review" and app.approved_at is None:
            raise RuleViolation("needs_approval", "Waiting for your approval", retry_later=True)
        if campaign.approval_mode == "auto_above" and app.approved_at is None and (app.score or 0) < campaign.auto_above_score:
            raise RuleViolation("needs_approval", "Waiting for your approval", retry_later=True)
    if not posting.is_open:
        raise RuleViolation("posting_closed", "The posting has closed")
    if config.max_posting_age_days is not None and not app.override_threshold:
        age, _ = posting_age_days(posting, now.astimezone(zone).date())
        if age > config.max_posting_age_days:
            raise RuleViolation("too_old", f"The posting is now {age} days old")
    block = blocked_reason(session, user.id, posting)
    if block:
        raise RuleViolation("blocked", block)
    if not in_window(config.send_window, zone, now):
        raise RuleViolation("outside_window", "Outside the send window", retry_later=True)
    if sent_today(session, user.id, zone, now, campaign.id) >= config.daily_quota:
        raise RuleViolation("campaign_quota", "Today's quota for this search is used up", retry_later=True)
    user_limit = min(user.daily_send_limit or 20, s.user_daily_send_ceiling)
    if sent_today(session, user.id, zone, now) >= user_limit:
        raise RuleViolation("user_quota", "Today's limit for your account is used up", retry_later=True)
    # Cooldowns count from the last sent application (section 10).
    if config.company_cooldown_days and app.company_id:
        since = now - timedelta(days=config.company_cooldown_days)
        hit = session.execute(
            select(Application.id).where(
                Application.user_id == user.id, Application.company_id == app.company_id,
                Application.sent_at >= since, Application.id != app.id,
            )
        ).first()
        if hit:
            raise RuleViolation("company_cooldown", f"You applied to this company in the last {config.company_cooldown_days} days")
    if config.contact_cooldown_days and not is_form:
        since = now - timedelta(days=config.contact_cooldown_days)
        hit = session.execute(
            select(Application.id).where(
                Application.user_id == user.id, Application.recipient == app.recipient,
                Application.sent_at >= since, Application.id != app.id,
            )
        ).first()
        if hit:
            raise RuleViolation("contact_cooldown", f"You wrote to this address in the last {config.contact_cooldown_days} days")
    last = session.execute(
        select(func.max(Application.sent_at)).where(Application.user_id == user.id)
    ).scalar()
    if last and (now - last) < timedelta(minutes=s.min_send_gap_minutes):
        raise RuleViolation("too_soon", "Too soon after the previous send", retry_later=True)
    if not slots.has_or_take_place(session, app, now.astimezone(zone).date()):
        raise RuleViolation("employer_limit", "Employer limit reached", retry_later=True)


def plan_send_times(
    count: int, now: datetime, window_end: datetime, rng: random.Random | None = None,
    min_gap_minutes: float | None = None,
) -> list[datetime]:
    """Plan every send time for the rest of the window (section 10).

    Gaps are random between 4 and 12 minutes. If they would run past the end they shrink
    evenly, never below the minimum. If the sends still cannot fit, fewer are planned."""
    s = get_settings()
    rng = rng or random.Random()
    min_gap = s.min_send_gap_minutes if min_gap_minutes is None else min_gap_minutes
    available = (window_end - now).total_seconds() / 60
    if count <= 0 or available <= 0:
        return []
    gaps = [rng.uniform(4, 12) for _ in range(count)]
    gaps[0] = rng.uniform(0.2, 2)  # the first send goes out soon after the window opens
    total = sum(gaps)
    if total > available:
        scale = available / total
        gaps = [max(min_gap, g * scale) if i else g * scale for i, g in enumerate(gaps)]
    times, t = [], now
    for g in gaps:
        t = t + timedelta(minutes=g)
        if t >= window_end:
            break
        times.append(t)
    return times


def registered(email: str) -> str:
    return registered_domain(domain_of(email))
