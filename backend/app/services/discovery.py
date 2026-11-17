"""Discovery run: collect new postings from the enabled sources (master document, section 7)."""

import logging
import uuid

from sqlalchemy.orm import Session

from app import audit
from app.connectors.fetcher import Fetcher
from app.connectors.sources import COMPANY_FEED_SOURCES, BoardMissing, RawPosting, SearchQuery, get_sources
from app.countries import ADZUNA_COUNTRIES
from app.llm.base import LLMClient
from app.models import Campaign
from app.services import postings as posting_service
from app.services import sites
from app.services.campaigns import get_config

log = logging.getLogger(__name__)
MAX_NEW_PER_RUN = 60


MAX_COUNTRIES = 5  # countries asked about one by one per search and run


def _countries_for(source: str, countries: list[str], per_country: bool = False) -> list[str]:
    """The countries one source is asked about. Sources that search one country per request (Adzuna, the web
    search, the federal board: `per_country`) are asked once for each chosen country, up to five (Adzuna only
    for the countries it covers, and not at all for "anywhere"). Whole boards and the job pool are asked once:
    they are not limited to a country (the pool receives all chosen countries in `countries`), and the hard
    filters then keep postings in any of the chosen countries."""
    chosen = countries[:MAX_COUNTRIES]
    if source == "adzuna":
        return [c for c in chosen if c in ADZUNA_COUNTRIES]
    if per_country and chosen:
        return chosen
    return [chosen[0] if chosen else ""]


def discover(session: Session, llm: LLMClient, campaign: Campaign, *, fetcher: Fetcher | None = None,
             mail_check=None, run_id: uuid.UUID | None = None) -> dict:
    config = get_config(campaign)
    sources = get_sources()
    found, created, errors = 0, 0, []
    # Every known source is searched, including ones added after the search was saved; demo mode adds its
    # sample source. Sources not installed (tests, demo) are skipped below.
    from app.services.campaigns import CampaignConfig

    names = list(dict.fromkeys([*config.sources, *CampaignConfig().sources, *[n for n in sources if n == "demo"]]))
    # The job pool goes first, so its already-collected jobs get the run's places before live searches do. While
    # a recent harvest covers a board, the board is not asked again here (see harvest.covered_sources).
    skip: set[str] = set()
    if "pool" in sources:
        from app.services import harvest

        names = ["pool", *[n for n in names if n != "pool"]]
        skip = harvest.covered_sources(config.keywords)
    def take(raws: list[RawPosting]) -> None:
        nonlocal found, created
        for raw in raws:
            found += 1
            if created >= MAX_NEW_PER_RUN:
                break
            try:
                _, is_new = posting_service.ingest(session, llm, raw, fetcher=fetcher, mail_check=mail_check)
                created += int(is_new)
            except Exception as exc:  # noqa: BLE001
                log.warning("ingest failed for %s: %s", raw.url, exc)
                errors.append(f"ingest: {type(exc).__name__}")

    from app.services import cvs as cv_service

    try:
        home = str(cv_service.get_fact_sheet(session, campaign.user_id).get("home_country") or "").lower()
    except Exception:  # noqa: BLE001 - no fact sheet yet
        home = ""
    checked = 0
    for name in names:
        source = sources.get(name)
        if source is None or name in skip:
            continue
        query_base = dict(keywords=config.keywords, location=(config.locations or [""])[0],
                          max_days_old=config.max_posting_age_days, home_country=home,
                          remote_ok=bool(config.remote_outside_countries or not config.countries)
                          and (not config.work_modes or "remote" in config.work_modes),
                          work_modes=list(config.work_modes), countries=list(config.countries),
                          languages=list(config.languages))
        extra = {"llm": llm} if getattr(source, "needs_llm", False) else {}
        if getattr(source, "needs_session", False):
            extra["session"] = session
        if name in COMPANY_FEED_SOURCES:
            # One company's own careers board at a time, from the directory, the ones checked longest ago first.
            for site in sites.due_company_boards(session, name):
                checked += 1
                try:
                    raws = source.search(SearchQuery(**query_base, country=(config.countries or [""])[0],
                                                     company_slugs=[site.slug]))
                except BoardMissing:
                    sites.record(site, ok=False, error="the board no longer exists", gone=True)
                    continue
                except Exception as exc:  # noqa: BLE001 - one failing board must not stop the others
                    sites.record(site, ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
                    continue
                sites.record(site, ok=True, jobs=len(raws), matches=len(raws))
                take(raws)
            continue
        site = sites.board(session, name) if name != "demo" else None
        if site is not None and not site.enabled:
            continue
        got, failed = 0, ""
        for country in _countries_for(name, config.countries, getattr(source, "per_country", False)):
            try:
                raws: list[RawPosting] = source.search(SearchQuery(**query_base, country=country), **extra)
            except Exception as exc:  # noqa: BLE001 - one failing source must not stop the others
                log.warning("source %s failed: %s", name, exc)
                errors.append(f"{name}: {type(exc).__name__}")
                failed = f"{type(exc).__name__}: {exc}"[:300]
                continue
            got += len(raws)
            take(raws)
        if site is not None:
            sites.record(site, ok=not failed or got > 0, jobs=got, matches=got, error=failed)
    result = {"found": found, "new": created, "errors": errors[:10], "company_boards_checked": checked,
              "left_to_pool": sorted(skip & set(names) & set(sources))}
    audit.record(session, user_id=campaign.user_id, actor="agent", tool="discover", run_id=run_id,
                 entity_id=campaign.id, result=result)
    return result
