"""The job pool: jobs the harvester collected from free public feeds, shared by every search.

Storing them here first means a board is read once for everyone, on the harvester's schedule, and a search only
pays (in model calls) for the pooled jobs that match it. See app/services/harvest.py for how the pool is filled.
"""

import re
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, case, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.connectors.sources import RawPosting, SearchQuery, matches_keywords
from app.countries import hiring_allows, normalize
from app.models import PoolJob, Posting

KEEP_FOR = timedelta(days=45)   # a job not seen on its board for this long is gone
MAX_TEXT = 20000

# Cities job ads name without a country. Two-letter parts are ignored ("CA" and "DE" are also US states).
_CITIES = {
    "berlin": "de", "munich": "de", "münchen": "de", "hamburg": "de", "frankfurt": "de", "frankfurt am main": "de",
    "cologne": "de", "köln": "de", "stuttgart": "de", "düsseldorf": "de", "leipzig": "de", "dresden": "de",
    "nuremberg": "de", "nürnberg": "de", "hanover": "de", "hannover": "de", "karlsruhe": "de", "bonn": "de",
    "deutschland": "de", "vienna": "at", "wien": "at", "graz": "at", "linz": "at", "salzburg": "at",
    "innsbruck": "at", "österreich": "at", "zurich": "ch", "zürich": "ch", "geneva": "ch", "basel": "ch",
    "bern": "ch", "lausanne": "ch", "schweiz": "ch", "amsterdam": "nl", "rotterdam": "nl", "utrecht": "nl",
    "eindhoven": "nl", "the hague": "nl", "london": "gb", "manchester": "gb", "edinburgh": "gb", "cambridge": "gb",
    "dublin": "ie", "paris": "fr", "lyon": "fr", "madrid": "es", "barcelona": "es", "lisbon": "pt", "porto": "pt",
    "warsaw": "pl", "krakow": "pl", "kraków": "pl", "wroclaw": "pl", "prague": "cz", "brno": "cz",
    "stockholm": "se", "copenhagen": "dk", "oslo": "no", "helsinki": "fi", "brussels": "be", "milan": "it",
    "rome": "it", "budapest": "hu", "bucharest": "ro", "tallinn": "ee", "riga": "lv", "vilnius": "lt",
    "luxembourg": "lu", "new york": "us", "san francisco": "us", "seattle": "us", "austin": "us", "boston": "us",
    "chicago": "us", "toronto": "ca", "vancouver": "ca", "montreal": "ca", "sydney": "au", "melbourne": "au",
    "singapore": "sg", "dubai": "ae", "bangalore": "in", "bengaluru": "in", "karachi": "pk", "lahore": "pk",
    "islamabad": "pk",
}


def guess_country(location: str) -> str | None:
    """The country a free-text location names ('Berlin, Germany', 'Remote - UK', 'Wien'), or None."""
    for part in reversed(re.split(r"[,/|;()–]|\s-\s", (location or "").lower())):
        part = " ".join(part.split())
        if len(part) < 3 and part not in ("uk", "us"):
            continue
        code = normalize(part) or _CITIES.get(part)
        if code:
            return code
    return None


def _clean(value, limit: int) -> str:
    return str(value or "").replace("\x00", "")[:limit]


def _number(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def known_keys(session: Session) -> set[tuple[str, str]]:
    """(source, source_id) of every pooled job, so a harvest can tell new jobs from ones it already has."""
    return {(source, ref) for source, ref in session.execute(select(PoolJob.source, PoolJob.source_id))}


def store(session: Session, raws: list[RawPosting], known: set[tuple[str, str]], now: datetime | None = None) -> list[RawPosting]:
    """Insert new jobs and refresh the last-seen time of known ones. Returns the new ones. A row that comes back
    without its text (the harvester skipped fetching it) keeps the text it already has."""
    from app.services.postings import dedupe_key

    now = now or datetime.now(UTC)
    rows, new, seen = [], [], set()
    for raw in raws:
        key = (_clean(raw.source, 32), _clean(raw.source_id, 500))
        if not key[1] or key in seen or not (raw.title or "").strip():
            continue
        seen.add(key)
        location = _clean(raw.location, 500)
        rows.append({
            "id": uuid.uuid4(), "source": key[0], "source_id": key[1], "url": _clean(raw.url, 4000),
            "title": _clean(raw.title, 500), "company": _clean(raw.company, 300),
            "description": _clean(raw.description, MAX_TEXT), "is_snippet": bool(raw.is_snippet or not raw.description),
            "location": location, "country": _clean(raw.country or guess_country(location) or "", 8).lower() or None,
            "posted_date": raw.posted_date if isinstance(raw.posted_date, date) else None,
            "work_mode": _clean(raw.work_mode, 16) or None, "salary_min": _number(raw.salary_min),
            "salary_max": _number(raw.salary_max), "contract_type": _clean(raw.contract_type, 100) or None,
            "hiring_regions": list(raw.hiring_regions) if raw.hiring_regions else None,
            "contact_email": _clean(raw.contact_email, 320) or None,
            "dedupe_key": dedupe_key(raw.company or "", raw.title or "", raw.location or ""),
            "first_seen": now, "last_seen": now,
        })
        if key not in known:
            new.append(raw)
    table = PoolJob.__table__.c
    for start in range(0, len(rows), 500):
        stmt = insert(PoolJob).values(rows[start:start + 500])
        ex = stmt.excluded
        has_text = ex.description != ""
        session.execute(stmt.on_conflict_do_update(index_elements=["source", "source_id"], set_={
            "last_seen": ex.last_seen,
            "url": func.coalesce(func.nullif(ex.url, ""), table.url),
            "title": func.coalesce(func.nullif(ex.title, ""), table.title),
            "location": func.coalesce(func.nullif(ex.location, ""), table.location),
            "description": func.coalesce(func.nullif(ex.description, ""), table.description),
            "is_snippet": case((has_text, ex.is_snippet), else_=table.is_snippet),
            "posted_date": func.coalesce(ex.posted_date, table.posted_date),
        }))
    known.update((r["source"], r["source_id"]) for r in rows)
    return new


def expire(session: Session, now: datetime | None = None) -> int:
    """Drop jobs their board has not listed for 45 days."""
    now = now or datetime.now(UTC)
    return session.execute(delete(PoolJob).where(PoolJob.last_seen < now - KEEP_FOR)).rowcount or 0


def stats(session: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    total = session.execute(select(func.count()).select_from(PoolJob)).scalar() or 0
    fresh = session.execute(select(func.count()).select_from(PoolJob).where(PoolJob.first_seen >= now - timedelta(days=1))).scalar() or 0
    return {"jobs": int(total), "new_last_day": int(fresh)}


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _blocked(session: Session) -> tuple[set[str], set[str], set[str]]:
    """(companies, domains, addresses) the session's user blocked, plus employers who asked not to be contacted.
    The same lists the hard filters check (matching.blocked_reason), read once per search."""
    from app.models import Block, GlobalBlock
    from app.services.postings import normalize_company

    user_id = session.info.get("user_id")
    rows = list(session.execute(select(GlobalBlock.type, GlobalBlock.value)))
    if user_id:
        rows += list(session.execute(select(Block.type, Block.value).where(Block.user_id == user_id)))
    out: dict[str, set[str]] = {"company": set(), "domain": set(), "address": set()}
    for kind, value in rows:
        if kind in out:
            out[kind].add(normalize_company(value) if kind == "company" else value.strip().lower())
    return out["company"], out["domain"], out["address"]


class PoolSource:
    """The job pool as a source for one search. Every job returned here is later read by the model, so
    everything the hard filters would reject anyway is dropped first, in code: keywords not in the text, a
    country outside the search (unless remote and remote jobs from elsewhere are welcome), a stated work mode the
    search does not want, too old, a remote job that cannot hire where the candidate lives, a blocked employer,
    and jobs already read as a posting (by anyone: postings are shared, so a job read once is already a candidate
    for every search). Newest first, capped at what one discovery run can read. No model calls."""

    name = "pool"
    needs_session = True
    SCAN = 2000    # rows read from the database per search
    LIMIT = 80     # discovery reads at most 60 new jobs a run; a few spare for ones that turn out to be duplicates

    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(UTC))

    def search(self, query: SearchQuery, session: Session | None = None) -> list[RawPosting]:
        if session is None:
            return []
        from app.services.postings import domain_of, normalize_company, registered_domain

        now = self.clock()
        stmt = select(PoolJob).where(~exists().where(Posting.dedupe_key == PoolJob.dedupe_key),
                                     ~exists().where(Posting.url == PoolJob.url, PoolJob.url != ""))
        if query.max_days_old:
            since = now - timedelta(days=query.max_days_old)
            stmt = stmt.where(or_(PoolJob.posted_date >= since.date(),
                                  and_(PoolJob.posted_date.is_(None), PoolJob.first_seen >= since)))
        countries = query.countries or ([query.country] if query.country else [])
        if countries:
            where = PoolJob.country.in_(countries)
            if query.remote_ok:
                where = or_(where, PoolJob.work_mode == "remote")
            stmt = stmt.where(where)
        if query.work_modes:
            stmt = stmt.where(or_(PoolJob.work_mode.is_(None), PoolJob.work_mode.in_(query.work_modes)))
        words = [k.strip() for k in query.keywords if k.strip()]
        if words:  # a rough database filter first; the exact whole-word check follows below
            stmt = stmt.where(or_(*[c.ilike(_like(w), escape="\\") for w in words for c in (PoolJob.title, PoolJob.description)]))
        stmt = stmt.order_by(PoolJob.posted_date.desc().nulls_last(), PoolJob.first_seen.desc()).limit(self.SCAN)
        companies, domains, addresses = _blocked(session)
        out = []
        for row in session.execute(stmt).scalars():
            if not matches_keywords(f"{row.title} {row.description}", query.keywords):
                continue
            if row.hiring_regions and query.home_country and hiring_allows(row.hiring_regions, query.home_country) is False:
                continue  # a remote job that cannot hire where the candidate lives
            if row.company and normalize_company(row.company) in companies:
                continue
            email = (row.contact_email or "").lower()
            if email and (email in addresses or registered_domain(domain_of(email)) in domains):
                continue
            out.append(RawPosting(
                source=row.source, source_id=row.source_id[:255], url=row.url, title=row.title, company=row.company,
                description=row.description, location=row.location, country=row.country, posted_date=row.posted_date,
                salary_min=row.salary_min, salary_max=row.salary_max, contract_type=row.contract_type,
                work_mode=row.work_mode, is_snippet=row.is_snippet or not row.description,
                contact_email=row.contact_email, hiring_regions=row.hiring_regions,
            ))
        # Jobs that give an address to write to come first: they can be applied to directly by email, which works
        # far more often than a web form. Within each group the newest first (the query's order, kept by sort).
        from app.services.postings import EMAIL_RE

        out.sort(key=lambda r: 0 if (r.contact_email or EMAIL_RE.search(r.description or "")) else 1)
        return out[:self.LIMIT]
