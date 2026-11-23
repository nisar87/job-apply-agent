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
from app.models import Application, Campaign, Company, Match, Message, PoolJob, Posting, User
from app.services import rules

log = logging.getLogger(__name__)

FREQUENCIES = ("daily", "weekly", "off")
DAILY_TARGET = 5  # applications a day the agent works towards; lower if the user's searches allow fewer
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


def target_per_day(session: Session, user: User) -> int:
    """How many applications a day the user wants: their searches' daily quotas, at most DAILY_TARGET."""
    from app.services.campaigns import get_config

    active = session.execute(select(Campaign).where(Campaign.user_id == user.id, Campaign.state == "active")).scalars()
    quota = sum(get_config(c).daily_quota for c in active)
    return min(quota, DAILY_TARGET) if quota else DAILY_TARGET


def funnel(session: Session, user: User, since: datetime, now: datetime) -> dict:
    """Each step from new jobs to emails sent in the period, and the step where the target was lost."""
    def count(model, *conds) -> int:
        return int(session.execute(select(func.count()).select_from(model).where(*conds)).scalar() or 0)

    in_period = (Match.user_id == user.id, Match.created_at >= since, Match.created_at < now)
    written = (Application.user_id == user.id, Application.route == "email", Application.created_at >= since,
               Application.created_at < now)
    days = max(1, round((now - since).total_seconds() / 86400))
    f = {
        "new_jobs": count(PoolJob, PoolJob.first_seen >= since, PoolJob.first_seen < now),
        "checked": count(Match, *in_period),
        "good": count(Match, *in_period, Match.decision.in_(("apply", "manual"))),
        "with_email": count(Match, *in_period, Match.decision == "apply"),
        "written": count(Application, *written),
        "stopped": count(Application, *written, Application.status == "failed"),
        "sent": count(Application, Application.user_id == user.id, Application.route == "email",
                      Application.sent_at >= since, Application.sent_at < now),
        "target": target_per_day(session, user) * days,
    }
    f["short_at"] = shortfall(f)
    return f


def shortfall(f: dict) -> str:
    """In words, the first step that kept the period below its target; empty when the target was met."""
    t = f["target"]
    if f["sent"] >= t:
        return ""
    if f["good"] < t:
        return (f"Too few good matches ({f['good']} for a target of {t}): the agent needs more matching jobs, so "
                "more job sources or wider search terms.")
    if f["with_email"] < t:
        return (f"Too few good matches with an email address ({f['with_email']} of {f['good']}): most matching "
                "jobs take applications only through a web form.")
    if f["written"] - f["stopped"] < t:
        return (f"Emails stopped by the quality checks: {f['stopped']} of {f['written']} did not pass.")
    return ("Enough emails were ready, but not all went out yet: they wait for the send window, the daily "
            "limit, or the pause between two emails to the same company.")


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
    f = funnel(session, user, since, now)
    return {
        "since": since, "now": now, "sent": sent, "waiting": waiting, "by_hand": by_hand, "checked": f["checked"],
        "good": f["good"], "funnel": f,
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

    needs = len(d["waiting"]) + len(d["by_hand"]) + len(d["replies"])
    lines += [f"In short: {n} applied · {len(d['replies'])} {'reply' if len(d['replies']) == 1 else 'replies'} · "
              f"{d['checked']} jobs checked",
              f"Needs you: {needs}" if needs else "Nothing needs you.", f"Open your agent: {base}/", ""]

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
    f = d["funnel"]
    lines += ["", f"From new jobs to emails sent (target: {f['target']})", "-" * 40,
              f"New jobs collected from all sources:   {f['new_jobs']}",
              f"Checked against your CV:               {f['checked']}",
              f"Good matches:                          {f['good']}",
              f"  with an employer's email address:    {f['with_email']}",
              f"Emails written:                        {f['written']}",
              f"  stopped by the quality checks:       {f['stopped']}",
              f"Emails sent:                           {f['sent']}",
              "Target reached." if not f["short_at"] else f"Where it fell short: {f['short_at']}", "",
              f"Open your agent: {base}/", f"All applications: {base}/applications",
              f"Change or stop this report: {base}/settings#reports", ""]
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
