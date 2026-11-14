"""Job sources behind one interface (master document, section 7).

Tier 1 are aggregator APIs; tier 2 are public per-company feeds of hiring systems.
Adding a country means enabling or adding sources here; the agent does not change.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol

import httpx

from app.config import get_settings
from app.connectors.fetcher import html_to_text


@dataclass
class SearchQuery:
    keywords: list[str] = field(default_factory=list)
    country: str = "at"
    location: str = ""
    max_days_old: int | None = None
    company_slugs: list[str] = field(default_factory=list)  # for per-company feeds


@dataclass
class RawPosting:
    source: str
    source_id: str
    url: str
    title: str
    company: str
    description: str = ""
    location: str = ""
    country: str | None = None
    posted_date: date | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    contract_type: str | None = None
    work_mode: str | None = None
    is_snippet: bool = False  # the source gave only part of the description
    contact_email: str | None = None


class JobSource(Protocol):
    name: str

    def search(self, query: SearchQuery) -> list[RawPosting]: ...


def _matches(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def _date(value) -> date | None:
    if not value:
        return None
    try:
        if isinstance(value, int | float):
            ts = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(ts, UTC).date()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, OSError):
        return None


class AdzunaSource:
    name = "adzuna"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        s = get_settings()
        if not (s.adzuna_app_id and s.adzuna_app_key):
            return []
        params = {
            "app_id": s.adzuna_app_id, "app_key": s.adzuna_app_key, "results_per_page": 50,
            "what_or": " ".join(query.keywords), "sort_by": "date", "content-type": "application/json",
        }
        if query.location:
            params["where"] = query.location
        if query.max_days_old:
            params["max_days_old"] = query.max_days_old
        resp = self.http.get(f"https://api.adzuna.com/v1/api/jobs/{query.country}/search/1", params=params)
        resp.raise_for_status()
        out = []
        for r in resp.json().get("results", []):
            out.append(RawPosting(
                source=self.name, source_id=str(r.get("id")), url=r.get("redirect_url", ""),
                title=html_to_text(r.get("title", "")), company=(r.get("company") or {}).get("display_name", ""),
                description=html_to_text(r.get("description", "")),
                location=(r.get("location") or {}).get("display_name", ""), country=query.country,
                posted_date=_date(r.get("created")), salary_min=r.get("salary_min"), salary_max=r.get("salary_max"),
                contract_type=r.get("contract_time") or r.get("contract_type"), is_snippet=True,
            ))
        return out


class ArbeitnowSource:
    name = "arbeitnow"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        resp = self.http.get("https://www.arbeitnow.com/api/job-board-api")
        resp.raise_for_status()
        out = []
        for r in resp.json().get("data", []):
            text = html_to_text(r.get("description", ""))
            if not _matches(f"{r.get('title', '')} {text} {' '.join(r.get('tags', []))}", query.keywords):
                continue
            out.append(RawPosting(
                source=self.name, source_id=str(r.get("slug")), url=r.get("url", ""), title=r.get("title", ""),
                company=r.get("company_name", ""), description=text, location=r.get("location", ""),
                posted_date=_date(r.get("created_at")), work_mode="remote" if r.get("remote") else None,
                contract_type=", ".join(r.get("job_types", [])) or None,
            ))
        return out


class GreenhouseSource:
    name = "greenhouse"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        out = []
        for slug in query.company_slugs:
            resp = self.http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
            if resp.status_code != 200:
                continue
            for r in resp.json().get("jobs", []):
                text = html_to_text(html_to_text(r.get("content", "")))  # content is HTML-escaped HTML
                if not _matches(f"{r.get('title', '')} {text}", query.keywords):
                    continue
                out.append(RawPosting(
                    source=self.name, source_id=f"{slug}:{r.get('id')}", url=r.get("absolute_url", ""),
                    title=r.get("title", ""), company=r.get("company_name") or slug, description=text,
                    location=(r.get("location") or {}).get("name", ""),
                    posted_date=_date(r.get("first_published") or r.get("updated_at")),
                ))
        return out


class LeverSource:
    name = "lever"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        out = []
        for slug in query.company_slugs:
            resp = self.http.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
            if resp.status_code != 200:
                continue
            for r in resp.json():
                text = r.get("descriptionPlain", "") + "\n" + "\n".join(
                    html_to_text(item.get("content", "")) for item in r.get("lists", [])
                )
                if not _matches(f"{r.get('text', '')} {text}", query.keywords):
                    continue
                cats = r.get("categories") or {}
                mode = (r.get("workplaceType") or "").lower()
                out.append(RawPosting(
                    source=self.name, source_id=f"{slug}:{r.get('id')}", url=r.get("hostedUrl", ""),
                    title=r.get("text", ""), company=slug, description=text.strip(), location=cats.get("location", ""),
                    posted_date=_date(r.get("createdAt")), contract_type=cats.get("commitment"),
                    work_mode={"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}.get(mode),
                ))
        return out


class PersonioSource:
    name = "personio"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        out = []
        for slug in query.company_slugs:
            resp = self.http.get(f"https://{slug}.jobs.personio.de/xml", params={"language": "en"})
            if resp.status_code != 200:
                continue
            try:
                root = ET.fromstring(resp.text)
            except ET.ParseError:
                continue
            for pos in root.iter("position"):
                def t(tag, pos=pos):
                    node = pos.find(tag)
                    return (node.text or "").strip() if node is not None and node.text else ""

                text = "\n\n".join(
                    f"{(d.findtext('name') or '').strip()}\n{html_to_text(d.findtext('value') or '')}"
                    for d in pos.iter("jobDescription")
                )
                if not _matches(f"{t('name')} {text}", query.keywords):
                    continue
                out.append(RawPosting(
                    source=self.name, source_id=f"{slug}:{t('id')}", url=f"https://{slug}.jobs.personio.de/job/{t('id')}",
                    title=t("name"), company=t("subcompany") or slug, description=text, location=t("office"),
                    posted_date=_date(t("createdAt")), contract_type=t("schedule") or t("employmentType") or None,
                ))
        return out


class FakeSource:
    """Returns whatever postings a test loads into it."""

    name = "fake"

    def __init__(self, postings: list[RawPosting] | None = None):
        self.postings = postings or []

    def search(self, query: SearchQuery) -> list[RawPosting]:
        return [p for p in self.postings if _matches(f"{p.title} {p.description}", query.keywords)]


COMPANY_FEED_SOURCES = {"greenhouse", "lever", "personio"}
_registry: dict[str, JobSource] | None = None


def set_sources(sources: dict[str, JobSource] | None) -> None:
    global _registry
    _registry = sources


def get_sources() -> dict[str, JobSource]:
    global _registry
    if _registry is None:
        _registry = {
            s.name: s for s in (AdzunaSource(), ArbeitnowSource(), GreenhouseSource(), LeverSource(), PersonioSource())
        }
    return _registry
