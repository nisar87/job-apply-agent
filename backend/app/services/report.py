"""The report of what the agent did, emailed to the user's main address (not the job Gmail).

Daily (default, at the user's chosen local hour), weekly (Mondays) or off. For every application sent it says
which job, the link to the ad, where it went (the address, or the web form), where that address came from (the
ad itself, or the page of the employer's site it was found on) and which source found the job, so the user can
see at a glance that the agent applies to the right jobs. It also lists drafts waiting for approval, jobs to
apply to by hand, replies, and the numbers for the period.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Application, Company, Match, Message, Posting, User
from app.services import rules

log = logging.getLogger(__name__)

FREQUENCIES = ("daily", "weekly", "off")
SOURCE_NAMES = {
    "arbeitsagentur": "German federal job board", "arbeitnow": "Arbeitnow", "remotive": "Remotive", "jobicy": "Jobicy",
    "himalayas": "Himalayas", "weworkremotely": "We Work Remotely", "remoteok": "Remote OK",
    "workingnomads": "Working Nomads", "hackernews": "Hacker News (Who is hiring)",
    "workablesearch": "Workable job search", "landingjobs": "Landing.jobs",
    "berlinstartupjobs": "Berlin Startup Jobs", "websearch": "Google search", "adzuna": "Adzuna", "greenhouse": "Greenhouse",
    "lever": "Lever", "ashby": "Ashby", "workable": "Workable", "smartrecruiters": "SmartRecruiters",
    "recruitee": "Recruitee", "personio": "Personio", "teamtailor": "Teamtailor", "link": "a link you added",
    "user_link": "a link you added",
    "manual": "a link you added",
}


def _last_key(user_id: uuid.UUID) -> str:
    return f"report:last:{user_id}"


def _kv():
    from app.tasks.locks import get_redis

    return get_redis()


def where_from(posting: Posting, app: Application) -> str:
    """Where the address the application went to came from, in words."""
    if app.route == "form":
        return f"the employer's web form ({posting.url})"
    page = (posting.fields or {}).get("contact_source")
    if page:
        return f"published on the employer's website: {page}"
    if app.recipient and app.recipient.lower() in (posting.description or "").lower():
        return "written in the job ad"
    return "given by the job source"


def build(session: Session, user: User, since: datetime, now: datetime) -> dict:
    def rows(*conds, order=Application.updated_at.desc()):
        q = (select(Application, Posting, Company).join(Posting, Posting.id == Application.posting_id)
             .outerjoin(Company, Company.id == Application.company_id).where(Application.user_id == user.id, *conds)
             .order_by(order))
        return session.execute(q).all()

    def item(app: Application, posting: Posting, company: Company | None) -> dict:
        return {
            "company": company.name if company else "", "title": posting.title, "link": posting.url or "",
            "to": app.recipient or "", "route": app.route, "where_from": where_from(posting, app),
            "found_by": SOURCE_NAMES.get(posting.source, posting.source), "score": app.score,
            "location": (posting.fields or {}).get("location") or "", "sent_at": app.sent_at, "status": app.status,
        }

    sent = [item(*r) for r in rows(Application.sent_at >= since, Application.sent_at < now, order=Application.sent_at)]
    waiting = [item(*r) for r in rows(Application.status.in_(("awaiting_approval", "queued")))]
    by_hand = [item(*r) for r in rows(Application.status.in_(("manual_apply", "form_ready")))]
    replies = session.execute(
        select(Message, Application, Company).join(Application, Application.id == Message.application_id)
        .outerjoin(Company, Company.id == Application.company_id)
        .where(Message.user_id == user.id, Message.direction == "in", Message.received_at >= since)
        .order_by(Message.received_at)
    ).all()
    checked = int(session.execute(select(func.count()).select_from(Match).where(
        Match.user_id == user.id, Match.created_at >= since)).scalar() or 0)
    good = int(session.execute(select(func.count()).select_from(Match).where(
        Match.user_id == user.id, Match.created_at >= since, Match.decision.in_(("apply", "manual")))).scalar() or 0)
    return {
        "since": since, "now": now, "sent": sent, "waiting": waiting, "by_hand": by_hand, "checked": checked,
        "good": good,
        "replies": [{"company": c.name if c else "", "subject": m.subject or a.subject, "from": m.sender or ""}
                    for m, a, c in replies],
    }


def render(user: User, d: dict, frequency: str) -> tuple[str, str]:
    """(subject, plain-text body)."""
    zone = rules.user_zone(user)
    base = get_settings().frontend_url.rstrip("/")
    period = "today" if frequency == "daily" else "this week"
    n = len(d["sent"])
    subject = (f"Report: {n} application{'s' if n != 1 else ''} sent {period}"
               + (f", {len(d['waiting'])} waiting for you" if d["waiting"] else ""))
    first = (user.name or "").split(" ")[0] or "there"
    lines = [f"Hi {first},", "", f"Here is what your job agent did {period} "
             f"({d['since'].astimezone(zone):%d %b %H:%M} to {d['now'].astimezone(zone):%d %b %H:%M}).", ""]

    def block(title: str, items: list[dict], sent: bool = False) -> None:
        lines.append(f"{title} ({len(items)})")
        lines.append("-" * len(lines[-1]))
        if not items:
            lines.extend(["None.", ""])
            return
        for i, it in enumerate(items, 1):
            head = f"{i}. {it['title']}" + (f" at {it['company']}" if it["company"] else "")
            lines.append(head + (f" ({it['location']})" if it["location"] else ""))
            if it["link"]:
                lines.append(f"   Job ad: {it['link']}")
            if it["route"] == "email" and it["to"]:
                lines.append(f"   {'Sent to' if sent else 'Would go to'}: {it['to']}")
                lines.append(f"   Address: {it['where_from']}")
            elif sent:
                lines.append(f"   Applied through {it['where_from']}")
            lines.append(f"   Found by: {it['found_by']}" + (f" · match {it['score']:.0f}/100" if it["score"] else ""))
            if sent and it["sent_at"]:
                lines.append(f"   Sent: {it['sent_at'].astimezone(zone):%d %b %H:%M}")
        lines.append("")

    block("Applications sent", d["sent"], sent=True)
    block("Waiting for your approval", d["waiting"])
    block("Apply on the employer's site (by hand)", d["by_hand"])
    lines.append(f"Replies from employers ({len(d['replies'])})")
    lines.append("-" * len(lines[-1]))
    lines.extend([f"- {r['company'] or r['from']}: {r['subject']}" for r in d["replies"]] or ["None."])
    lines += ["", f"Jobs checked against your CV: {d['checked']}, good matches: {d['good']}.", "",
              f"Approve drafts: {base}/drafts", f"All applications: {base}/applications",
              f"Change or stop this report: {base}/account", ""]
    return subject, "\n".join(lines)


def address_of(user: User) -> str:
    return user.escalation_email or user.email


def send(session: Session, user: User, now: datetime | None = None, *, since: datetime | None = None) -> bool:
    """Builds and emails one report to the user's main address. True when it went out."""
    from app.connectors.notify_email import get_notifier
    from app.services.account import _send_from_own_mailbox

    now = now or datetime.now(UTC)
    frequency = user.report_frequency if user.report_frequency in FREQUENCIES else "daily"
    if since is None:
        last = _kv().get(_last_key(user.id))
        since = datetime.fromisoformat(last) if last else now - timedelta(days=7 if frequency == "weekly" else 1)
    subject, body = render(user, build(session, user, since, now), frequency)
    to = address_of(user)
    try:
        ok = get_notifier().send(to, subject, body)
    except Exception as exc:  # noqa: BLE001 - fall back to the user's own mailbox
        log.warning("report via the product's mail failed: %s", type(exc).__name__)
        ok = False
    ok = ok or _send_from_own_mailbox(session, user, to, subject, body)
    if ok:
        _kv().set(_last_key(user.id), now.isoformat(), ex=40 * 86400)
    return ok


def due(user: User, now: datetime) -> bool:
    """Whether this user's report should go out now: past their hour, once a day (or on Mondays for weekly)."""
    if user.report_frequency not in ("daily", "weekly") or user.status != "active":
        return False
    local = now.astimezone(rules.user_zone(user))
    if local.hour < (user.report_hour if user.report_hour is not None else 20):
        return False
    if user.report_frequency == "weekly" and local.weekday() != 0:
        return False
    last = _kv().get(_last_key(user.id))
    if not last:
        return True
    last_local = datetime.fromisoformat(last).astimezone(rules.user_zone(user))
    return last_local.date() < local.date()


def send_due(now: datetime | None = None) -> int:
    """Called by the scheduler tick: sends every report that is due. Returns how many went out."""
    from app.db import system_session, user_session

    now = now or datetime.now(UTC)
    with system_session() as s:
        ids = [u.id for u in s.execute(select(User).where(User.status == "active")).scalars() if due(u, now)]
    sent = 0
    for user_id in ids:
        try:
            with user_session(user_id) as s:
                user = s.get(User, user_id)
                if user and due(user, now) and send(s, user, now):
                    sent += 1
        except Exception:  # noqa: BLE001 - one user's report must not stop the others
            log.exception("report for a user failed")
    return sent
