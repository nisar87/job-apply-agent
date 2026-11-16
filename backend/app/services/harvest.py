"""The job harvester: collects the latest jobs from free sources into the shared job pool, on its own schedule.

Free means public feeds and the official federal API only: no keys, no per-request price, no model calls, no
scraping of sites whose terms forbid it. Each run:

1. reads every free whole board (Arbeitsagentur per search term when Germany is searched, Arbeitnow's newest
   pages, the remote boards' whole feeds, kept to tech jobs, Hacker News' monthly "Who is hiring" thread, whose
   posts often give an address to apply to, and Workable's public job search per term and country), through
   cached fetches, so a board is never asked more often than every few hours; slow sources get a share of the
   run's time, not all of it;
2. scans each new job's link and text for a company's own careers board (Greenhouse, Lever, …) and adds it to
   the directory, which is how the pool reaches more companies every day; a slice of Common Crawl's free URL
   index adds many more boards (it carries on where the last run stopped, about one index a month);
3. checks the company boards in the directory that are due, several at once, taking turns between known boards
   and new ones, and stores all of their jobs until the run's time budget is spent; boards whose checks find no
   technical job are checked less often (see sites.due_company_boards);
4. drops jobs not seen for 45 days.

Searches then take matching jobs from the pool (app/services/pool.py PoolSource), and the agent reads only those.

Schedule: the five-minute scheduler tick starts a harvest every two hours (claimed in the key-value store, so
one instance starts it) in a background thread, so the tick itself returns quickly and no request limit can cut
the harvest off; the run stops itself after about four minutes. `POST /api/system/run/harvest` runs one at once
(for an operator or a separate scheduler job). A lock keeps two harvests from overlapping.
"""

import json
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import zip_longest

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.sources import (
    COMPANY_FEED_SOURCES,
    HARVEST_BOARDS,
    BoardMissing,
    HarvestQuery,
    SearchQuery,
    get_sources,
    matches_keywords,
)
from app.models import Campaign, JobSite, User
from app.services import pool, sites

log = logging.getLogger(__name__)

HARVEST_EVERY = timedelta(hours=2)
TIME_BUDGET_SECONDS = 240          # stop well before a scheduler request or worker limit could cut the run off
MAX_TERMS = 25
MAX_DAYS = 14                      # how far back the federal board is asked
FRESH_FOR = timedelta(hours=6)     # a harvest this recent lets searches skip the boards it covers
COMPANY_BOARDS_PER_RUN = 5000
BOARD_WORKERS = 6                  # company boards fetched at once
BOARD_CHUNK = 24                   # boards fetched between two looks at the clock
# Sources that may take only part of the run, so the company boards still get their turn.
SOURCE_BUDGET_SECONDS = {"arbeitsagentur": 70, "workablesearch": 50, "commoncrawl": 40, "hackernews": 30}
LAST_KEY, DUE_KEY, LOCK_KEY = "harvest:last", "harvest:due", "harvest:running"

# Remote OK and Working Nomads list every kind of job; the pool keeps the technical ones (judged by the title)
# and any job that matches someone's search terms.
TECH_ONLY = {"remoteok", "workingnomads"}
TECH_TERMS = ["developer", "engineer", "engineering", "software", "programmer", "devops", "sre", "backend",
              "frontend", "full stack", "fullstack", "full-stack", "data", "machine learning", "ml", "ai", "cloud",
              "security", "qa", "test automation", "architect", "python", "java", "javascript", "typescript",
              "golang", "rust", "c#", ".net", "php", "ruby", "react", "node", "kubernetes", "android", "ios",
              "mobile", "web", "infrastructure", "platform", "database"]
# Boards whose pooled jobs do not depend on the search terms (whole feeds), and those that do.
WHOLE_FEEDS = {"arbeitnow", "remotive", "jobicy", "weworkremotely"}
TERM_FEEDS = {"arbeitsagentur", "himalayas", "remoteok", "workingnomads"}


def search_terms(session: Session) -> tuple[list[str], list[str]]:
    """Keywords and countries of every active search, the most shared keywords first (at most 25)."""
    from app.services.campaigns import get_config

    words, countries = Counter(), []
    rows = session.execute(select(Campaign).join(User, User.id == Campaign.user_id)
                           .where(Campaign.state != "paused", User.status == "active")).scalars()
    for campaign in rows:
        try:
            config = get_config(campaign)
        except Exception:  # noqa: BLE001 - a search saved under older rules still counts for what it can
            continue
        words.update({k.strip().lower() for k in config.keywords if k.strip()})
        countries += [c for c in config.countries if c not in countries]
    return [w for w, _ in words.most_common(MAX_TERMS)], countries


def harvest(session: Session, now: datetime | None = None, *, budget_seconds: float = TIME_BUDGET_SECONDS,
            clock=time.monotonic) -> dict:
    """One harvest run (see the module docstring). Commits as it goes, so the work done survives a later failure."""
    now = now or datetime.now(UTC)
    started = clock()

    def out_of_time() -> bool:
        return clock() - started > budget_seconds

    terms, countries = search_terms(session)
    sources = get_sources()
    known = pool.known_keys(session)
    boards_before = _company_boards(session)
    result: dict = {"started_at": now.isoformat(), "terms": terms, "countries": countries, "sources": {},
                    "new": 0, "boards_checked": 0, "boards_learned": 0, "boards_left": 0, "expired": 0, "errors": []}
    query = HarvestQuery(terms=terms, countries=countries, max_days_old=MAX_DAYS,
                         is_known=lambda source, ref: (source, ref) in known, out_of_time=out_of_time)

    def keep(raws) -> list:
        """Pool the jobs, learn the company boards the new ones point to, and commit."""
        new = pool.store(session, raws, known, now)
        for r in new:
            sites.learn(session, r.url or "", r.description or "")
        result["new"] += len(new)
        session.commit()
        return new

    for name in HARVEST_BOARDS:
        source = sources.get(name)
        if source is None:
            continue
        if out_of_time():
            result["errors"].append(f"{name}: not reached in time")
            continue
        if not sites.board(session, name).enabled:
            continue  # paused by the operator or after failures
        own_start, own_budget = clock(), SOURCE_BUDGET_SECONDS.get(name)
        query.out_of_time = (lambda s=own_start, b=own_budget: out_of_time() or (b is not None and clock() - s > b))
        try:
            if hasattr(source, "find_boards"):
                links = source.find_boards(query)
                boards = sites.find_company_boards(*links)
                sites.add(session, boards, name)
                session.commit()
                result["sources"][name] = {"links": len(links), "boards": len(boards)}
                continue
            if hasattr(source, "harvest"):
                raws = source.harvest(query)
            else:
                raws = source.search(SearchQuery(keywords=[], country=(countries or [""])[0], countries=countries))
            if name in TECH_ONLY:
                raws = [r for r in raws if matches_keywords(r.title, TECH_TERMS)
                        or (terms and matches_keywords(f"{r.title} {r.description}", terms))]
            new = keep(raws)
            result["sources"][name] = {"jobs": len(raws), "new": len(new)}
        except Exception as exc:  # noqa: BLE001 - one failing board must not stop the others
            session.rollback()
            log.warning("harvest: %s failed: %s", name, exc)
            result["errors"].append(f"{name}: {type(exc).__name__}")
    query.out_of_time = out_of_time

    # Company boards: every one that is due, taking turns between boards checked before (oldest check first) and
    # boards never checked (oldest learned first), so a flood of newly found boards cannot starve the known ones.
    # Several boards are fetched at once (different companies, mostly different hosts); results are stored here.
    rechecks = sites.due_company_boards(session, None, now, limit=COMPANY_BOARDS_PER_RUN, checked=True)
    fresh = sites.due_company_boards(session, None, now, limit=COMPANY_BOARDS_PER_RUN, checked=False)
    due = [s for pair in zip_longest(rechecks, fresh) for s in pair if s is not None][:COMPANY_BOARDS_PER_RUN]
    due = [s for s in due if s.provider in COMPANY_FEED_SOURCES and sources.get(s.provider) is not None]
    with ThreadPoolExecutor(max_workers=BOARD_WORKERS) as workers:
        for start in range(0, len(due), BOARD_CHUNK):
            if out_of_time():
                result["boards_left"] = len(due) - start
                break
            chunk = due[start:start + BOARD_CHUNK]
            jobs = [workers.submit(_read_board, sources[s.provider], s.slug) for s in chunk]
            for site, job in zip(chunk, jobs, strict=True):
                result["boards_checked"] += 1
                outcome, payload = job.result()
                if outcome == "gone":
                    sites.record(site, ok=False, error="the board no longer exists", gone=True, now=now)
                    session.commit()
                    continue
                if outcome == "error":
                    sites.record(site, ok=False, error=payload, now=now)
                    session.commit()
                    continue
                raws = payload
                matching = sum(1 for r in raws if terms and matches_keywords(f"{r.title} {r.description}", terms))
                useful = matching > 0 or any(matches_keywords(r.title, TECH_TERMS) for r in raws)
                sites.record(site, ok=True, jobs=len(raws), matches=matching, now=now, useful=useful)
                session.commit()
                counts = result["sources"].setdefault(site.provider, {"jobs": 0, "new": 0, "boards": 0})
                try:
                    new = keep(raws)
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    log.warning("harvest: storing %s/%s failed: %s", site.provider, site.slug, exc)
                    result["errors"].append(f"{site.provider}/{site.slug}: {type(exc).__name__}")
                    new = []
                counts["jobs"] += len(raws)
                counts["new"] += len(new)
                counts["boards"] += 1

    result["expired"] = pool.expire(session, now)
    session.commit()
    # Counted, not summed from the inserts: an insert that skips a known board does not reliably report 0 rows.
    result["boards_learned"] = _company_boards(session) - boards_before
    result["finished_at"] = (now + timedelta(seconds=clock() - started)).isoformat()
    result["errors"] = result["errors"][:30]
    return result


def _read_board(source, slug: str) -> tuple[str, object]:
    """One company's whole board, in a worker thread (network only, no database)."""
    try:
        return "ok", source.search(SearchQuery(keywords=[], company_slugs=[slug]))
    except BoardMissing:
        return "gone", None
    except Exception as exc:  # noqa: BLE001 - one failing board must not stop the others
        return "error", f"{type(exc).__name__}: {exc}"[:300]


def _company_boards(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(JobSite).where(JobSite.kind == "company")).scalar() or 0)


def last_result() -> dict | None:
    from app.tasks.locks import get_redis

    try:
        raw = get_redis().get(LAST_KEY)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def run(now: datetime | None = None) -> dict:
    """Run one harvest now, unless one is already running. Used by the scheduler thread and the system endpoint."""
    from app.db import system_session
    from app.tasks.locks import get_redis

    kv = get_redis()
    if not kv.set(LOCK_KEY, "1", nx=True, ex=TIME_BUDGET_SECONDS + 900):
        return {"busy": True}
    try:
        with system_session() as session:
            result = harvest(session, now)
        kv.set(LAST_KEY, json.dumps(result, default=str), ex=14 * 86400)
        return result
    finally:
        kv.delete(LOCK_KEY)


def _run_quietly() -> None:
    try:
        run()
    except Exception:  # noqa: BLE001 - logged; the next run is two hours away
        log.exception("harvest failed")


def start_if_due(now: datetime | None = None) -> bool:
    """Called by every scheduler tick: starts a harvest in a background thread once every two hours. The key
    is claimed atomically, so only one tick (and one instance) starts it."""
    from app.tasks.locks import get_redis

    if not get_redis().set(DUE_KEY, (now or datetime.now(UTC)).isoformat(), nx=True,
                           ex=int(HARVEST_EVERY.total_seconds())):
        return False
    threading.Thread(target=_run_quietly, name="harvest", daemon=True).start()
    return True


def covered_sources(keywords: list[str], now: datetime | None = None) -> set[str]:
    """Live sources a search may skip because a recent harvest already put their jobs in the pool: company boards
    and whole feeds always; the term-based boards only when every keyword of the search was a harvest term (a
    search created since the last harvest still asks them itself). Nothing is skipped when the last harvest is
    older than six hours or never ran, or for a board that failed in it."""
    last = last_result()
    now = now or datetime.now(UTC)
    if not last or not last.get("finished_at"):
        return set()
    try:
        finished = datetime.fromisoformat(last["finished_at"])
    except ValueError:
        return set()
    if now - finished > FRESH_FOR:
        return set()
    done = set(last.get("sources") or {})
    covered = set(COMPANY_FEED_SOURCES) | (WHOLE_FEEDS & done)
    harvested = {t.lower() for t in last.get("terms") or []}
    wanted = {k.strip().lower() for k in keywords if k.strip()}
    if wanted and wanted <= harvested:
        covered |= TERM_FEEDS & done
        if "de" not in (last.get("countries") or []):
            covered.discard("arbeitsagentur")
    return covered
