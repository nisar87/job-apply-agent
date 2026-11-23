"""Decides what is due for every user and campaign (master document, section 4).

The tick runs every five minutes. Each piece of work is safe to repeat: runs are
locked per user, sends are idempotent and discovery deduplicates.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.agent import runs as agent_runs
from app.connectors.fetcher import Fetcher
from app.db import system_session, user_session
from app.llm import SpendCapExceeded
from app.llm.access import client_for
from app.models import Application, Campaign, Run, User
from app.services import rules, slots
from app.services.campaigns import get_config
from app.tasks.locks import LockBusy, get_redis, run_lock

log = logging.getLogger(__name__)
DISCOVERY_EVERY = timedelta(hours=6)
PREPARE_EVERY = timedelta(hours=1)
PREPARE_LEAD = timedelta(hours=2)  # start drafting this long before the window opens


def _last_run(session, campaign_id: uuid.UUID, run_type: str) -> datetime | None:
    return session.execute(
        select(func.max(Run.created_at)).where(Run.campaign_id == campaign_id, Run.type == run_type)
    ).scalar()


def _active_users() -> list[uuid.UUID]:
    with system_session() as s:
        rows = s.execute(
            select(Campaign.user_id).join(User, User.id == Campaign.user_id)
            .where(User.status == "active").distinct()
        ).scalars()
        return list(rows)


def tick_user(user_id: uuid.UUID, now: datetime | None = None, fetcher: Fetcher | None = None, **discover_kw) -> dict:
    now = now or datetime.now(UTC)
    done: dict = {"discovery": 0, "prepare": 0, "sent": 0, "errors": []}
    with user_session(user_id) as s:
        user = s.get(User, user_id)
        zone = rules.user_zone(user)
        campaign_ids = [c.id for c in s.execute(select(Campaign).where(Campaign.user_id == user_id, Campaign.state != "paused")).scalars()]
    for cid in campaign_ids:
        try:
            with user_session(user_id) as s:
                campaign = s.get(Campaign, cid)
                config = get_config(campaign)
                last = _last_run(s, cid, "discovery")
                if last is None or now - last >= DISCOVERY_EVERY:
                    with run_lock(user_id, "discovery"):
                        agent_runs.discovery_run(s, client_for(s, user_id), campaign, fetcher=fetcher, **discover_kw)
                        done["discovery"] += 1
            with user_session(user_id) as s:
                campaign = s.get(Campaign, cid)
                bounds = rules.window_bounds(config.send_window, zone, now)
                near_window = bool(bounds and bounds[0] - PREPARE_LEAD <= now < bounds[1])
                waiting = int(s.execute(select(func.count()).select_from(Application).where(
                    Application.campaign_id == cid, Application.status.in_(("awaiting_approval", "queued")))).scalar() or 0)
                last = _last_run(s, cid, "prepare")
                remaining = config.daily_quota - rules.sent_today(s, user_id, zone, now, cid)
                if near_window and waiting < remaining and (last is None or now - last >= PREPARE_EVERY):
                    with run_lock(user_id, "prepare"):
                        agent_runs.prepare_run(s, client_for(s, user_id), campaign, now)
                        done["prepare"] += 1
        except LockBusy:
            continue
        except SpendCapExceeded as exc:   # includes NoModelKey
            done["errors"].append(str(exc) or "spend cap reached")
        except Exception as exc:  # noqa: BLE001 - one campaign's failure must not stop the rest
            log.exception("tick failed for campaign %s", cid)
            done["errors"].append(f"{type(exc).__name__}")
    try:
        with run_lock(user_id, "dispatch"), user_session(user_id) as s:
            done["sent"] = len(agent_runs.dispatch_run(s, user_id, now)["sent"])
    except LockBusy:
        pass
    done["inbox"] = inbox_user(user_id, now)
    return done


def inbox_user(user_id: uuid.UUID, now: datetime | None = None) -> dict:
    """Inbox sync, follow-ups and reminders for one user. Also called when Gmail pushes a change."""
    from app.llm.access import llm_for
    from app.services import inbox

    now = now or datetime.now(UTC)
    try:
        with run_lock(user_id, "inbox"), user_session(user_id) as s:
            llm = llm_for(s, user_id)
            result = inbox.sync_inbox(s, llm, user_id, now)
            result.update(inbox.followup_run(s, llm, user_id, now))
            result["reminders"] = inbox.renotify_urgent(s, user_id, now)
            from app.services import account

            result["emailed"] = account.deliver_notifications(s, user_id, now)
            return result
    except LockBusy:
        return {"busy": True}
    except SpendCapExceeded:
        return {"error": "spend cap reached"}
    except Exception as exc:  # noqa: BLE001 - one bad mailbox (wrong password, timeout) must not fail the whole pass
        log.warning("inbox pass failed for %s: %s", user_id, type(exc).__name__)
        return {"error": f"mailbox: {type(exc).__name__}"}


def tick(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    summary = {"users": 0, "sent": 0}
    from app.services import report

    try:  # reports go out even while sending is paused: they tell the user what happened
        summary["reports"] = report.send_due(now)
    except Exception:  # noqa: BLE001
        log.exception("reports failed")
    if get_redis().get("global_pause"):
        return {**summary, "paused": True}
    from app.services import harvest

    # The job harvester runs every two hours in its own thread, so this tick is not held up (see harvest.py).
    summary["harvest_started"] = harvest.start_if_due(now)
    # One queued job per user: a slow or failing user never delays the others (roadmap phase 6). With a worker
    # the jobs run in parallel across worker processes; inline (development, tests) they run one after another.
    from app.tasks import dispatch
    from app.tasks.jobs import tick_user_task

    for user_id in _active_users():
        result = dispatch.enqueue(tick_user_task, str(user_id), now.isoformat())
        summary["users"] += 1
        if isinstance(result, dict):
            summary["sent"] += int(result.get("sent", 0))
            summary["failed"] = summary.get("failed", 0) + (1 if result.get("failed") else 0)
    return summary


HEALTH_KEY = "health:tick"


def record_health(user_id: uuid.UUID, started: datetime, seconds: float, errors: list[str]) -> None:
    """What the operator sees per user: when the last pass ran, how long it took and what went wrong."""
    import json

    try:
        get_redis().hset(HEALTH_KEY, str(user_id), json.dumps({
            "at": started.isoformat(), "seconds": round(seconds, 1), "errors": errors[:5]}))
    except Exception:  # noqa: BLE001 - a failed health note must never stop or fail a background pass
        log.warning("could not record the background pass for %s", user_id)


def health() -> dict[str, dict]:
    import json

    try:
        return {k: json.loads(v) for k, v in get_redis().hgetall(HEALTH_KEY).items()}
    except Exception:  # noqa: BLE001
        return {}


def allocate_places(now: datetime | None = None) -> int:
    """Daily allocation of places on contested postings (section 10). Runs once per day."""
    now = now or datetime.now(UTC)
    key = f"allocated:{now.date().isoformat()}"
    if not get_redis().set(key, "1", nx=True, ex=36 * 3600):
        return 0
    with system_session() as s:
        return len(slots.allocate(s, now.date()))
