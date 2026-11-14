"""Discovery run: collect new postings from the enabled sources (master document, section 7)."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.connectors.fetcher import Fetcher
from app.connectors.sources import COMPANY_FEED_SOURCES, RawPosting, SearchQuery, get_sources
from app.countries import ADZUNA_COUNTRIES
from app.llm.base import LLMClient
from app.models import Campaign, Company
from app.services import postings as posting_service
from app.services.campaigns import get_config

log = logging.getLogger(__name__)
MAX_NEW_PER_RUN = 60


def _countries_for(source: str, countries: list[str]) -> list[str]:
    """The countries one source is asked about. Adzuna searches one country per request, so it is asked
    once for each chosen country it covers (and not at all for "anywhere"). Every other source searches
    its whole board in one request; the country filter then keeps only postings in the chosen countries."""
    if source == "adzuna":
        return [c for c in countries if c in ADZUNA_COUNTRIES]
    return [countries[0] if countries else ""]


def discover(session: Session, llm: LLMClient, campaign: Campaign, *, fetcher: Fetcher | None = None,
             mail_check=None, run_id: uuid.UUID | None = None) -> dict:
    config = get_config(campaign)
    sources = get_sources()
    found, created, errors = 0, 0, []
    names = list(config.sources) + [n for n in sources if n == "demo"]   # demo mode adds its sample source
    for name in names:
        source = sources.get(name)
        if source is None:
            continue
        slugs: list[str] = []
        if name in COMPANY_FEED_SOURCES:
            slugs = [c.hiring_slug for c in session.execute(
                select(Company).where(Company.hiring_system == name, Company.hiring_slug.is_not(None))
            ).scalars()]
            if not slugs:
                continue
        for country in _countries_for(name, config.countries):
            query = SearchQuery(keywords=config.keywords, country=country,
                                location=(config.locations or [""])[0], max_days_old=config.max_posting_age_days,
                                company_slugs=slugs)
            try:
                raws: list[RawPosting] = source.search(query)
            except Exception as exc:  # noqa: BLE001 - one failing source must not stop the others
                log.warning("source %s failed: %s", name, exc)
                errors.append(f"{name}: {type(exc).__name__}")
                continue
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
    result = {"found": found, "new": created, "errors": errors[:10]}
    audit.record(session, user_id=campaign.user_id, actor="agent", tool="discover", run_id=run_id,
                 entity_id=campaign.id, result=result)
    return result
