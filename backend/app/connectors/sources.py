"""Job sources behind one interface (master document, section 7).

Tier 1 are aggregator APIs; tier 2 are public per-company feeds of hiring systems.
Adding a country means enabling or adding sources here; the agent does not change.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol

import httpx

from app.config import get_settings
from app.connectors.fetcher import html_to_text
from app.countries import split_hiring_labels


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
    hiring_regions: list[str] | None = None  # where a remote job may hire from, as the board states it


class JobSource(Protocol):
    name: str

    def search(self, query: SearchQuery) -> list[RawPosting]: ...


def keyword_pattern(keyword: str) -> re.Pattern:
    """A keyword as a whole term: "git" does not match "digital", "java" does not match "javascript".
    A keyword that starts or ends with a symbol keeps that side open, so ".net" still matches "asp.net"."""
    k = keyword.strip().lower()
    left = r"(?<![a-z0-9])" if k[:1].isalnum() else ""
    right = r"(?![a-z0-9])" if k[-1:].isalnum() else ""
    return re.compile(left + re.escape(k) + right)


def matches_keywords(text: str, keywords: list[str]) -> bool:
    keywords = [k for k in keywords if k.strip()]
    if not keywords:
        return True
    low = text.lower()
    return any(keyword_pattern(k).search(low) for k in keywords)


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
            if not matches_keywords(f"{r.get('title', '')} {text} {' '.join(r.get('tags', []))}", query.keywords):
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
            if resp.status_code == 404:
                raise BoardMissing(slug)
            resp.raise_for_status()
            for r in resp.json().get("jobs", []):
                text = html_to_text(html_to_text(r.get("content", "")))  # content is HTML-escaped HTML
                if not matches_keywords(f"{r.get('title', '')} {text}", query.keywords):
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
            if resp.status_code == 404:
                raise BoardMissing(slug)
            resp.raise_for_status()
            for r in resp.json():
                text = r.get("descriptionPlain", "") + "\n" + "\n".join(
                    html_to_text(item.get("content", "")) for item in r.get("lists", [])
                )
                if not matches_keywords(f"{r.get('text', '')} {text}", query.keywords):
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
            if resp.status_code == 404:
                raise BoardMissing(slug)
            resp.raise_for_status()
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
                if not matches_keywords(f"{t('name')} {text}", query.keywords):
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
        return [p for p in self.postings if matches_keywords(f"{p.title} {p.description}", query.keywords)]


def demo_postings() -> list[RawPosting]:
    """Sample postings for demo mode (LLM_PROVIDER=fake), so the app can be tried offline."""
    today = datetime.now(UTC).date()
    rows = [
        ("Backend Engineer (Python)", "Alpenblick Software GmbH", "python, postgresql, docker", "jobs@alpenblick.example"),
        ("Senior Python Developer", "Donauwerk AG", "python, fastapi, kubernetes", "karriere@donauwerk.example"),
        ("Data Engineer", "Salzach Analytics", "python, sql, airflow", "hr@salzach.example"),
        ("Platform Engineer", "Wienzeile Digital", "python, docker", ""),
        ("SAP Consultant", "Grazer Beratung KG", "sap, abap", "jobs@grazer.example"),
        ("Python Developer, Core Banking", "Innbank Systems", "python, cobol, mainframe", "jobs@innbank.example"),
    ]
    return [RawPosting(source="demo", source_id=f"demo-{i}", url=f"https://example.com/demo-job-{i}", title=t, company=c,
                       description=f"{t} at {c}. Remote within Austria. Full-time.\nRequirements: {r}\n"
                                   + (f"Send your application to {e}\n" if e else "Apply through our careers page.\n"),
                       location="Vienna", country="at", posted_date=today) for i, (t, c, r, e) in enumerate(rows)]


def _cached_json(http: httpx.Client, url: str, ttl: int, **params):
    """GET a public feed at most once per `ttl` seconds for all users and searches together, so the boards'
    request limits are respected however many searches run. Stored in the shared key-value store."""
    import json

    from app.tasks.locks import get_redis

    key = "src:" + url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    try:
        cached = get_redis().get(key)
    except Exception:  # noqa: BLE001 - no store: just fetch
        cached = None
    if cached:
        return json.loads(cached)
    resp = http.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    try:
        get_redis().set(key, json.dumps(data), ex=ttl)
    except Exception:  # noqa: BLE001
        pass
    return data


class RemotiveSource:
    """Remote jobs from remotive.com. Terms: link back to the job on Remotive and name Remotive as the source;
    no more than about four requests a day, so the software category is fetched once every six hours."""

    name = "remotive"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "JobApplyAgent/0.1"})

    def search(self, query: SearchQuery) -> list[RawPosting]:
        data = _cached_json(self.http, "https://remotive.com/api/remote-jobs", 6 * 3600, category="software-dev")
        out = []
        for r in data.get("jobs", []):
            text = html_to_text(r.get("description", ""))
            if not matches_keywords(f"{r.get('title', '')} {' '.join(r.get('tags') or [])} {text}", query.keywords):
                continue
            out.append(RawPosting(
                source=self.name, source_id=str(r.get("id")), url=r.get("url", ""), title=r.get("title", ""),
                company=r.get("company_name", ""), description=text, location=r.get("candidate_required_location", ""),
                posted_date=_date(r.get("publication_date")), work_mode="remote",
                contract_type=r.get("job_type") or None,
                hiring_regions=split_hiring_labels(r.get("candidate_required_location")),
            ))
        return out


class JobicySource:
    """Remote jobs from jobicy.com. Terms: credit Jobicy and send applicants to the job's own URL.
    The latest 100 engineering jobs are fetched at most every six hours."""

    name = "jobicy"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "JobApplyAgent/0.1"})

    def search(self, query: SearchQuery) -> list[RawPosting]:
        data = _cached_json(self.http, "https://jobicy.com/api/v2/remote-jobs", 6 * 3600, count=100, industry="engineering")
        out = []
        for r in data.get("jobs", []):
            text = html_to_text(r.get("jobDescription") or r.get("jobExcerpt") or "")
            if not matches_keywords(f"{r.get('jobTitle', '')} {text}", query.keywords):
                continue
            out.append(RawPosting(
                source=self.name, source_id=str(r.get("id")), url=r.get("url", ""), title=html_to_text(r.get("jobTitle", "")),
                company=r.get("companyName", ""), description=text, location=r.get("jobGeo", ""),
                posted_date=_date(r.get("pubDate")), work_mode="remote",
                salary_min=r.get("salaryMin") or r.get("annualSalaryMin"), salary_max=r.get("salaryMax") or r.get("annualSalaryMax"),
                contract_type=", ".join(r.get("jobType") or []) if isinstance(r.get("jobType"), list) else r.get("jobType"),
                hiring_regions=split_hiring_labels(r.get("jobGeo")),
            ))
        return out


class HimalayasSource:
    """Remote jobs from himalayas.app. Terms: keep the application link unchanged, name Himalayas as the source,
    never pass the jobs on to other boards. Their data refreshes daily, so each search is cached for 12 hours."""

    name = "himalayas"
    MAX_TERMS = 4

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "JobApplyAgent/0.1"})

    def search(self, query: SearchQuery) -> list[RawPosting]:
        seen, out = set(), []
        for term in [k for k in query.keywords if k.strip()][:self.MAX_TERMS]:
            data = _cached_json(self.http, "https://himalayas.app/jobs/api/search", 12 * 3600, q=term, page=1)
            for r in data.get("jobs", []):
                guid = str(r.get("guid") or r.get("applicationLink"))
                if guid in seen:
                    continue
                text = html_to_text(r.get("description") or r.get("excerpt") or "")
                if not matches_keywords(f"{r.get('title', '')} {text}", query.keywords):
                    continue
                seen.add(guid)
                out.append(RawPosting(
                    source=self.name, source_id=guid, url=r.get("applicationLink", ""), title=r.get("title", ""),
                    company=r.get("companyName", ""), description=text,
                    location=", ".join(r.get("locationRestrictions") or []) or "Worldwide",
                    posted_date=_date(r.get("pubDate")), work_mode="remote",
                    salary_min=r.get("minSalary"), salary_max=r.get("maxSalary"),
                    contract_type=r.get("employmentType") or None,
                    hiring_regions=split_hiring_labels(r.get("locationRestrictions") or []) or ["Worldwide"],
                ))
        return out


class BoardMissing(Exception):
    """A company's board no longer exists on that hiring system (404 or an empty company)."""


def _mode(remote, hybrid=False) -> str | None:
    return "remote" if remote else ("hybrid" if hybrid else None)


def _rss_items(text: str) -> list[dict]:
    """Items of an RSS feed as {tag: text}, namespaces dropped (so 'tt:locations' becomes 'locations')."""
    root = ET.fromstring(text)
    out = []
    for item in root.iter("item"):
        row = {}
        for child in item:
            tag = child.tag.split("}", 1)[-1]
            row[tag] = (child.text or "").strip()
        out.append(row)
    return out


class WeWorkRemotelySource:
    """We Work Remotely RSS feeds (back-end and full-stack programming). Each item links back to its post."""

    name = "weworkremotely"
    FEEDS = ("https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
             "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss")

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "JobApplyAgent/0.1"})

    def search(self, query: SearchQuery) -> list[RawPosting]:
        from app.tasks.locks import get_redis

        out, seen = [], set()
        for feed in self.FEEDS:
            key = "src:" + feed
            try:
                text = get_redis().get(key)
            except Exception:  # noqa: BLE001
                text = None
            if not text:
                resp = self.http.get(feed)
                resp.raise_for_status()
                text = resp.text
                try:
                    get_redis().set(key, text, ex=6 * 3600)
                except Exception:  # noqa: BLE001
                    pass
            for r in _rss_items(text):
                company, _, role = r.get("title", "").partition(": ")
                role = role or company
                body = html_to_text(r.get("description", ""))
                guid = r.get("guid") or r.get("link", "")
                if guid in seen or not matches_keywords(f"{role} {r.get('skills', '')} {body}", query.keywords):
                    continue
                seen.add(guid)
                out.append(RawPosting(
                    source=self.name, source_id=guid, url=r.get("link", ""), title=role, company=company if role != company else "",
                    description=body, location=r.get("region", ""), posted_date=_rss_date(r.get("pubDate")),
                    work_mode="remote", contract_type=r.get("type") or None,
                    hiring_regions=split_hiring_labels(r.get("region") or r.get("country")),
                ))
        return out


def _rss_date(value: str | None) -> date | None:
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(value).date() if value else None
    except (TypeError, ValueError):
        return _date(value)


class RemoteOkSource:
    """Remote OK's public API. Terms: link back to the job on Remote OK and name it as the source. The first
    array item is their legal notice; jobs follow. Fetched at most every six hours."""

    name = "remoteok"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0 (compatible; JobApplyAgent/0.1)"})

    def search(self, query: SearchQuery) -> list[RawPosting]:
        data = _cached_json(self.http, "https://remoteok.com/api", 6 * 3600)
        out = []
        for r in data if isinstance(data, list) else []:
            if not isinstance(r, dict) or not r.get("id") or not r.get("position"):
                continue
            text = html_to_text(r.get("description", ""))
            if not matches_keywords(f"{r.get('position', '')} {' '.join(r.get('tags') or [])} {text}", query.keywords):
                continue
            out.append(RawPosting(
                source=self.name, source_id=str(r.get("id")), url=r.get("url") or r.get("apply_url", ""),
                title=r.get("position", ""), company=r.get("company", ""), description=text,
                location=r.get("location", ""), posted_date=_date(r.get("date") or r.get("epoch")), work_mode="remote",
                salary_min=r.get("salary_min") or None, salary_max=r.get("salary_max") or None,
                hiring_regions=split_hiring_labels(r.get("location")) or ["Worldwide"],
            ))
        return out


class WorkingNomadsSource:
    """Working Nomads' exposed jobs feed (small, all remote)."""

    name = "workingnomads"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "JobApplyAgent/0.1"})

    def search(self, query: SearchQuery) -> list[RawPosting]:
        data = _cached_json(self.http, "https://www.workingnomads.com/api/exposed_jobs/", 6 * 3600)
        out = []
        for r in data if isinstance(data, list) else []:
            text = html_to_text(r.get("description", ""))
            if not matches_keywords(f"{r.get('title', '')} {r.get('tags', '')} {text}", query.keywords):
                continue
            out.append(RawPosting(
                source=self.name, source_id=r.get("url", ""), url=r.get("url", ""), title=r.get("title", ""),
                company=r.get("company_name", ""), description=text, location=r.get("location", ""),
                posted_date=_date(r.get("pub_date")), work_mode="remote",
                hiring_regions=split_hiring_labels(r.get("location")),
            ))
        return out


class AshbySource:
    """One company's Ashby job board (public posting API)."""

    name = "ashby"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        out = []
        for slug in query.company_slugs:
            resp = self.http.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"})
            if resp.status_code == 404:
                raise BoardMissing(slug)
            resp.raise_for_status()
            for r in resp.json().get("jobs", []):
                if r.get("isListed") is False:
                    continue
                text = r.get("descriptionPlain") or html_to_text(r.get("descriptionHtml", ""))
                if not matches_keywords(f"{r.get('title', '')} {text}", query.keywords):
                    continue
                remote = bool(r.get("isRemote")) or (r.get("workplaceType") or "").lower() == "remote"
                out.append(RawPosting(
                    source=self.name, source_id=f"{slug}:{r.get('id')}", url=r.get("jobUrl") or r.get("applyUrl", ""),
                    title=r.get("title", ""), company=slug, description=text, location=r.get("location", ""),
                    posted_date=_date(r.get("publishedAt")), contract_type=r.get("employmentType"),
                    work_mode=_mode(remote, (r.get("workplaceType") or "").lower() == "hybrid"),
                ))
        return out


class WorkableSource:
    """One company's Workable board (the public widget endpoint Workable's own embed uses). The listing has no
    description, so it carries the title and location; the posting page is read when the agent looks closer."""

    name = "workable"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        out = []
        for slug in query.company_slugs:
            resp = self.http.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            if resp.status_code == 404:
                raise BoardMissing(slug)
            resp.raise_for_status()
            data = resp.json()
            for r in data.get("jobs", []):
                where = ", ".join(x for x in (r.get("city"), r.get("country")) if x)
                if not matches_keywords(f"{r.get('title', '')} {r.get('department', '')}", query.keywords):
                    continue
                out.append(RawPosting(
                    source=self.name, source_id=f"{slug}:{r.get('shortcode')}", url=r.get("url") or r.get("application_url", ""),
                    title=r.get("title", ""), company=data.get("name") or slug, description=f"{r.get('title', '')}\n{where}",
                    location=where, posted_date=_date(r.get("published_on") or r.get("created_at")),
                    contract_type=r.get("employment_type"), work_mode=_mode(r.get("telecommuting")), is_snippet=True,
                ))
        return out


class SmartRecruitersSource:
    """One company's SmartRecruiters postings. Unknown companies answer with zero results, not 404."""

    name = "smartrecruiters"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        out = []
        for slug in query.company_slugs:
            resp = self.http.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100})
            if resp.status_code == 404:
                raise BoardMissing(slug)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("totalFound") and not data.get("content"):
                raise BoardMissing(slug)
            for r in data.get("content", []):
                loc = r.get("location") or {}
                text = " ".join(str(x) for x in (r.get("name"), (r.get("function") or {}).get("label"),
                                                  (r.get("experienceLevel") or {}).get("label")) if x)
                if not matches_keywords(text, query.keywords):
                    continue
                out.append(RawPosting(
                    source=self.name, source_id=f"{slug}:{r.get('id')}",
                    url=f"https://jobs.smartrecruiters.com/{slug}/{r.get('id')}", title=r.get("name", ""),
                    company=(r.get("company") or {}).get("name") or slug, description=text,
                    location=loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                    country=(loc.get("country") or "").lower() or None, posted_date=_date(r.get("releasedDate")),
                    contract_type=(r.get("typeOfEmployment") or {}).get("label"),
                    work_mode=_mode(loc.get("remote"), loc.get("hybrid")), is_snippet=True,
                ))
        return out


class RecruiteeSource:
    """One company's Recruitee offers (public careers API; the token-protected API is a different one)."""

    name = "recruitee"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        out = []
        for slug in query.company_slugs:
            resp = self.http.get(f"https://{slug}.recruitee.com/api/offers/")
            if resp.status_code == 404:
                raise BoardMissing(slug)
            resp.raise_for_status()
            for r in resp.json().get("offers", []):
                text = html_to_text(f"{r.get('description', '')}\n{r.get('requirements', '')}")
                if not matches_keywords(f"{r.get('title', '')} {text}", query.keywords):
                    continue
                out.append(RawPosting(
                    source=self.name, source_id=f"{slug}:{r.get('id') or r.get('slug')}",
                    url=r.get("careers_url") or r.get("careers_apply_url", ""), title=r.get("title", ""),
                    company=r.get("company_name") or slug, description=text, location=r.get("location", ""),
                    country=(r.get("country_code") or "").lower() or None, posted_date=_date(r.get("published_at")),
                    work_mode=_mode(r.get("remote"), r.get("hybrid")),
                ))
        return out


class TeamtailorSource:
    """One company's Teamtailor jobs feed (public RSS)."""

    name = "teamtailor"

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)

    def search(self, query: SearchQuery) -> list[RawPosting]:
        out = []
        for slug in query.company_slugs:
            resp = self.http.get(f"https://{slug}.teamtailor.com/jobs.rss")
            if resp.status_code == 404:
                raise BoardMissing(slug)
            resp.raise_for_status()
            for r in _rss_items(resp.text):
                text = html_to_text(r.get("description", ""))
                if not matches_keywords(f"{r.get('title', '')} {text}", query.keywords):
                    continue
                status = (r.get("remoteStatus") or "").lower()
                out.append(RawPosting(
                    source=self.name, source_id=f"{slug}:{r.get('guid') or r.get('link')}", url=r.get("link", ""),
                    title=r.get("title", ""), company=slug, description=text, location=r.get("locations", ""),
                    posted_date=_rss_date(r.get("pubDate")),
                    work_mode="remote" if status in ("fully", "remote") else ("hybrid" if status == "hybrid" else None),
                ))
        return out


REMOTE_SOURCES = {"remotive", "jobicy", "himalayas", "weworkremotely", "remoteok", "workingnomads"}
COMPANY_FEED_SOURCES = {"greenhouse", "lever", "personio", "ashby", "workable", "smartrecruiters", "recruitee",
                        "teamtailor"}
_registry: dict[str, JobSource] | None = None


def set_sources(sources: dict[str, JobSource] | None) -> None:
    global _registry
    _registry = sources


def get_sources() -> dict[str, JobSource]:
    global _registry
    if _registry is None:
        _registry = {
            s.name: s for s in (AdzunaSource(), ArbeitnowSource(), GreenhouseSource(), LeverSource(), PersonioSource(),
                                RemotiveSource(), JobicySource(), HimalayasSource(), WeWorkRemotelySource(),
                                RemoteOkSource(), WorkingNomadsSource(), AshbySource(), WorkableSource(),
                                SmartRecruitersSource(), RecruiteeSource(), TeamtailorSource())
        }
        if get_settings().llm_provider == "fake":
            _registry = {"demo": FakeSource(demo_postings())}
            _registry["demo"].name = "demo"
    return _registry
