"""Places on a posting, shared across users (master document, section 10).

This is the one place where data is counted across users. The table holds only a
posting, a day and counts. Nothing here reveals who else applied.
"""

import random
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Application, PostingSlot

PENDING = ("awaiting_approval", "queued")


def _slot(session: Session, posting_id: uuid.UUID, day: date) -> PostingSlot:
    row = session.execute(
        select(PostingSlot).where(PostingSlot.posting_id == posting_id, PostingSlot.day == day).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = PostingSlot(posting_id=posting_id, day=day, held=0, sent=0)
        session.add(row)
        session.flush()
    return row


def total_sent(session: Session, posting_id: uuid.UUID) -> int:
    return int(session.execute(
        select(func.coalesce(func.sum(PostingSlot.sent), 0)).where(PostingSlot.posting_id == posting_id)
    ).scalar() or 0)


def has_or_take_place(session: Session, app: Application, day: date) -> bool:
    """True if this application may be sent today. Takes a free place if one is left."""
    s = get_settings()
    if total_sent(session, app.posting_id) >= s.posting_cap_total:
        return False
    slot = _slot(session, app.posting_id, day)
    if app.place_day == day:
        return True
    if slot.sent + slot.held < s.posting_cap_per_day:
        slot.held += 1
        app.place_day = day
        session.flush()
        return True
    return False


def release_place(session: Session, app: Application) -> None:
    """Give an unused place back (rejected, expired, or window closed without sending)."""
    if app.place_day is None:
        return
    slot = _slot(session, app.posting_id, app.place_day)
    slot.held = max(0, slot.held - 1)
    app.place_day = None
    session.flush()


def mark_sent(session: Session, app: Application, day: date) -> None:
    slot = _slot(session, app.posting_id, day)
    if app.place_day == day:
        slot.held = max(0, slot.held - 1)
    slot.sent += 1
    app.place_day = None
    session.flush()


def allocate(session: Session, day: date, rng: random.Random | None = None) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Daily allocation for contested postings. Must run in a system session.

    Places go in order of the neutral score (default weights), ties broken at random,
    so a user's own weights cannot win a place."""
    s = get_settings()
    rng = rng or random.Random()
    # Yesterday's unused holds are void.
    for app in session.execute(select(Application).where(Application.place_day.is_not(None), Application.place_day < day)).scalars():
        app.place_day = None
    waiting = session.execute(select(Application).where(Application.status.in_(PENDING))).scalars().all()
    by_posting: dict[uuid.UUID, list[Application]] = {}
    for app in waiting:
        by_posting.setdefault(app.posting_id, []).append(app)
    result: dict[uuid.UUID, list[uuid.UUID]] = {}
    for posting_id, apps in by_posting.items():
        remaining_total = s.posting_cap_total - total_sent(session, posting_id)
        slot = _slot(session, posting_id, day)
        places = max(0, min(s.posting_cap_per_day - slot.sent, remaining_total))
        if len(apps) <= places and not any(a.place_day for a in apps):
            continue  # not contested: places are taken on the spot at send time
        apps.sort(key=lambda a: (-(a.neutral_score or 0.0), rng.random()))
        winners = apps[:places]
        for a in apps:
            a.place_day = day if a in winners else None
        slot.held = len(winners)
        result[posting_id] = [a.id for a in winners]
    session.flush()
    return result
