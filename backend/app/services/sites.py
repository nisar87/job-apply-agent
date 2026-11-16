"""The directory of places where jobs are looked for (job_sites).

It grows by itself: every posting the app reads is scanned for links to a company's own careers board on a
hiring system (Greenhouse, Lever, Ashby, …), and each new company found is added. Discovery then checks those
boards in turn, oldest first, and records how each check went; a board that keeps failing is paused.
"""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import JobSite

# (provider, pattern capturing the company's id on that hiring system). Checked against posting links and text.
_PATTERNS = [
    ("greenhouse", r"greenhouse\.io/embed/job_app\?(?:[^\s\"'<>]*&)?for=([\w-]+)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([^/?#\s\"'<>]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
    ("workable", r"https?://([\w-]+)\.workable\.com"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
    ("recruitee", r"https?://([\w-]+)\.recruitee\.com"),
    ("personio", r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)"),
    ("teamtailor", r"https?://([\w-]+)\.teamtailor\.com"),
]
_COMPILED = [(p, re.compile(rx, re.IGNORECASE)) for p, rx in _PATTERNS]
# Path parts that are part of the system's own site, never a company.
_NOT_A_COMPANY = {"embed", "api", "www", "apply", "careers", "jobs", "j", "o", "static", "assets", "app", "help",
                  "support", "blog", "docs", "v1", "v0", "posting-api", "job_app", "job-boards", "boards"}
CHECK_EVERY = timedelta(hours=12)       # a company board is checked at most twice a day
PER_DISCOVERY = 25                      # boards checked per discovery run, oldest first
PAUSE_AFTER_FAILURES = 5


def find_company_boards(*texts: str) -> set[tuple[str, str]]:
    """Every (provider, company id) mentioned in these links or texts."""
    found = set()
    for text in texts:
        for provider, rx in _COMPILED:
            for m in rx.finditer(text or ""):
                slug = unquote(m.group(1)).strip().strip(".-")
                if not slug or slug.lower() in _NOT_A_COMPANY or len(slug) > 120:
                    continue
                if provider != "ashby" and provider != "smartrecruiters":
                    slug = slug.lower()          # these systems treat ids case-insensitively
                found.add((provider, slug))
    return found


def learn(session: Session, url: str, text: str = "") -> int:
    """Add the company boards a posting points to. Returns how many were new."""
    boards = find_company_boards(url, text[:20000])
    new = 0
    for provider, slug in boards:
        result = session.execute(
            insert(JobSite).values(provider=provider, slug=slug, kind="company", name=slug, enabled=True,
                                   learned_from=url[:2000], url=board_url(provider, slug), last_jobs=0,
                                   last_matches=0, failures=0, last_error="")
            .on_conflict_do_nothing(index_elements=["provider", "slug"])
        )
        new += result.rowcount or 0
    return new


def board_url(provider: str, slug: str) -> str:
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{slug}", "lever": f"https://jobs.lever.co/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/{slug}", "workable": f"https://apply.workable.com/{slug}",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}", "recruitee": f"https://{slug}.recruitee.com",
        "personio": f"https://{slug}.jobs.personio.de", "teamtailor": f"https://{slug}.teamtailor.com",
    }.get(provider, "")


BOARD_URLS = {
    "pool": "/sources", "websearch": "https://www.google.com", "arbeitnow": "https://www.arbeitnow.com", "arbeitsagentur": "https://www.arbeitsagentur.de/jobsuche", "adzuna": "https://www.adzuna.com", "remotive": "https://remotive.com",
    "jobicy": "https://jobicy.com", "himalayas": "https://himalayas.app", "weworkremotely": "https://weworkremotely.com",
    "remoteok": "https://remoteok.com", "workingnomads": "https://www.workingnomads.com",
}


def board(session: Session, provider: str) -> JobSite:
    """The directory row of a whole job board, created on first use."""
    row = session.execute(select(JobSite).where(JobSite.provider == provider, JobSite.slug == "")).scalar_one_or_none()
    if row is None:
        session.execute(insert(JobSite).values(provider=provider, slug="", kind="board", name=provider, enabled=True,
                                               url=BOARD_URLS.get(provider, ""), learned_from="", last_jobs=0,
                                               last_matches=0, failures=0, last_error="")
                        .on_conflict_do_nothing(index_elements=["provider", "slug"]))
        row = session.execute(select(JobSite).where(JobSite.provider == provider, JobSite.slug == "")).scalar_one()
    return row


def due_company_boards(session: Session, provider: str | None, now: datetime | None = None, limit: int = PER_DISCOVERY) -> list[JobSite]:
    """Enabled company boards of one hiring system (or of all, when provider is None) that have not been checked
    recently, oldest check first."""
    now = now or datetime.now(UTC)
    q = select(JobSite).where(JobSite.kind == "company", JobSite.enabled.is_(True),
                              (JobSite.last_checked_at.is_(None)) | (JobSite.last_checked_at < now - CHECK_EVERY))
    if provider is not None:
        q = q.where(JobSite.provider == provider)
    rows = session.execute(q.order_by(JobSite.last_checked_at.asc().nulls_first()).limit(limit)).scalars().all()
    return list(rows)


def record(site: JobSite, *, ok: bool, jobs: int = 0, matches: int = 0, error: str = "", gone: bool = False,
           now: datetime | None = None) -> None:
    """Store how a check went. A board that no longer exists, or fails five times in a row, is paused."""
    now = now or datetime.now(UTC)
    site.last_checked_at = now
    if ok:
        site.last_ok_at, site.last_jobs, site.last_matches, site.failures, site.last_error = now, jobs, matches, 0, ""
        return
    site.failures = (site.failures or 0) + 1
    site.last_error = error[:500]
    if gone or site.failures >= PAUSE_AFTER_FAILURES:
        site.enabled = False
