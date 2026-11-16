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


class AdzunaLimit(Exception):
    """Adzuna refused because the plan's calls are used up (or the key is not active)."""


class AdzunaSource:
    """Adzuna (jobs from StepStone, Indeed and company sites, by country). Every request counts against the plan,
    so each one is made to bring back as many new, relevant jobs as possible:

    - one search term per request, in job titles only. Adzuna drops symbols, so a phrase like "C# .NET" turns into
      "c net" and pulls in bricklayers and "Net at Work GmbH"; ".net" alone in titles is about 90% real .NET jobs.
      Terms that shrink to a single letter ("C#") are not sent; multi-word keywords are left out.
    - only jobs since the last search of the same term (plus a day), newest first, 50 per page; a further page
      only when the page was full.
    - the terms take turns from one day to the next when the budget does not cover all of them.
    - one request per term a day at most: the answer is shared by every user and every run that day.
    - a daily (and optional monthly) budget for all users together; when Adzuna refuses, nothing more is asked
      until the next day.
    The postings are still checked against all the search's keywords here, and the agent reads each one in full."""

    name = "adzuna"
    BASE = "https://api.adzuna.com/v1/api/jobs"
    PER_PAGE = 50
    MAX_PAGES = 3

    def __init__(self, http: httpx.Client | None = None, clock=None):
        self.http = http or httpx.Client(timeout=20)
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def terms(keywords: list[str]) -> list[str]:
        out = []
        for k in keywords:
            k = k.strip().lower()
            if not k or " " in k or len(re.sub(r"[^a-z0-9]", "", k)) < 2:
                continue
            if k not in out:
                out.append(k)
        return out

    def _kv(self):
        from app.tasks.locks import get_redis

        return get_redis()

    def _spend(self) -> bool:
        """Takes one call from today's (and this month's) budget; False when none is left."""
        s, kv, now = get_settings(), self._kv(), self.clock()
        if kv.get("adzuna:paused:" + now.date().isoformat()):
            return False
        day, month = f"adzuna:calls:{now.date().isoformat()}", f"adzuna:calls:{now:%Y-%m}"
        if int(kv.get(day) or 0) >= s.adzuna_daily_calls:
            return False
        if s.adzuna_monthly_calls and int(kv.get(month) or 0) >= s.adzuna_monthly_calls:
            return False
        kv.incr(day), kv.expire(day, 2 * 86400)
        kv.incr(month), kv.expire(month, 32 * 86400)
        return True

    def _page(self, country: str, term: str, days: int, page: int, location: str) -> dict:
        import json

        s, kv = get_settings(), self._kv()
        # One answer per term and day, shared by every user and every run that day (runs come every few hours).
        cache = f"adzuna:result:{self.clock().date().isoformat()}:{country}:{term}:{location}:{page}"
        cached = kv.get(cache)
        if cached:
            return json.loads(cached)
        if not self._spend():
            raise AdzunaLimit("")  # our own budget: a normal pause, not a failure
        params = {"app_id": s.adzuna_app_id, "app_key": s.adzuna_app_key, "results_per_page": self.PER_PAGE,
                  "title_only": term, "sort_by": "date", "max_days_old": days, "content-type": "application/json"}
        if location:
            params["where"] = location
        resp = self.http.get(f"{self.BASE}/{country}/search/{page}", params=params)
        if resp.status_code in (401, 403, 429):
            kv.set("adzuna:paused:" + self.clock().date().isoformat(), "1", ex=2 * 86400)
            raise AdzunaLimit(f"Adzuna refused the request ({resp.status_code}): the plan's calls may be used up")
        resp.raise_for_status()
        data = resp.json()
        kv.set(cache, json.dumps(data), ex=26 * 3600)
        return data

    def search(self, query: SearchQuery) -> list[RawPosting]:
        s = get_settings()
        terms = self.terms(query.keywords)
        if not (s.adzuna_app_id and s.adzuna_app_key) or not terms or not query.country:
            return []
        kv, now = self._kv(), self.clock()
        # Start with the term searched longest ago, so every term gets its turn within the budget.
        def last(term: str) -> str:
            return kv.get(f"adzuna:last:{query.country}:{term}") or ""
        terms.sort(key=last)
        cap = query.max_days_old or 30
        seen, out, refused = set(), [], None
        for term in terms:
            previous = last(term)
            days = cap
            if previous:
                since = (now - datetime.fromisoformat(previous)).days + 1
                days = max(1, min(cap, since))
            got_any = False
            for page in range(1, self.MAX_PAGES + 1):
                try:
                    data = self._page(query.country, term, days, page, query.location)
                except AdzunaLimit as exc:
                    refused = exc
                    break
                got_any = True
                rows = data.get("results") or []
                for r in rows:
                    rid = str(r.get("id"))
                    title, text = html_to_text(r.get("title", "")), html_to_text(r.get("description", ""))
                    if rid in seen or not matches_keywords(f"{title} {text}", query.keywords):
                        continue
                    seen.add(rid)
                    out.append(RawPosting(
                        source=self.name, source_id=rid, url=r.get("redirect_url", ""), title=title,
                        company=(r.get("company") or {}).get("display_name", ""), description=text,
                        location=(r.get("location") or {}).get("display_name", ""), country=query.country,
                        posted_date=_date(r.get("created")), salary_min=r.get("salary_min"),
                        salary_max=r.get("salary_max"), contract_type=r.get("contract_time") or r.get("contract_type"),
                        is_snippet=True,
                    ))
                if len(rows) < self.PER_PAGE:
                    break
            if got_any:
                kv.set(f"adzuna:last:{query.country}:{term}", now.isoformat(), ex=60 * 86400)
            if refused:
                break
        if refused and str(refused) and not out:
            raise refused  # Adzuna itself refused: shown on the sources page; the other sources carry on
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


class ArbeitsagenturSource:
    """Germany's federal job board (Bundesagentur für Arbeit, jobsuche.api.bund.dev): most jobs advertised in
    Germany, new ones every day, no key. Searched only when Germany is one of the search's countries.

    One search per keyword (the board matches job titles), newest first; the full text of each job is fetched
    once and kept for three days, so repeated searches cost the board almost nothing."""

    name = "arbeitsagentur"
    BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
    MAX_TERMS = 6
    PER_TERM = 50
    MAX_DETAILS = 80

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)
        # The board's public client id, the same for everyone (not a secret).
        self.http.headers.update({"X-API-Key": "jobboerse-jobsuche", "User-Agent": "JobApplyAgent/0.1"})

    def _details(self, ref: str) -> dict:
        import base64

        code = base64.b64encode(ref.encode()).decode()
        try:
            return _cached_json(self.http, f"{self.BASE}/pc/v4/jobdetails/{code}", 3 * 24 * 3600)
        except Exception:  # noqa: BLE001 - a job without its full text is still worth listing
            return {}

    def search(self, query: SearchQuery) -> list[RawPosting]:
        if query.country != "de":
            return []
        days = min(max(query.max_days_old or 7, 1), 100)
        hits: dict[str, dict] = {}
        for term in [k.strip() for k in query.keywords if k.strip()][:self.MAX_TERMS]:
            params = {"was": term, "angebotsart": 1, "veroeffentlichtseit": days, "size": self.PER_TERM, "page": 1}
            if query.location:
                params["wo"] = query.location
            data = _cached_json(self.http, f"{self.BASE}/pc/v6/jobs", 6 * 3600, **params)
            for r in data.get("ergebnisliste") or []:
                ref = r.get("referenznummer")
                if ref and ref not in hits:
                    hits[ref] = r
        newest = sorted(hits.items(), key=lambda kv: kv[1].get("datumErsteVeroeffentlichung") or "", reverse=True)
        out = []
        for ref, r in newest[:self.MAX_DETAILS]:
            detail = self._details(ref)
            title = r.get("stellenangebotsTitel") or detail.get("stellenangebotsTitel") or ""
            text = detail.get("stellenangebotsBeschreibung") or ""
            if not matches_keywords(f"{title} {text}", query.keywords):
                continue
            place = ((r.get("stellenlokationen") or [{}])[0].get("adresse") or {})
            home = r.get("homeofficemoeglich", detail.get("homeofficemoeglich"))
            yearly = r.get("verguetungsangabe") == "JAHRESGEHALT"
            out.append(RawPosting(
                source=self.name, source_id=ref, title=title, company=r.get("firma", ""),
                url=r.get("externeURL") or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}",
                description=text, location=place.get("ort", ""), country="de",
                posted_date=_date(r.get("datumErsteVeroeffentlichung")),
                # The board leaves the home-office flag out unless home office is offered.
                work_mode="hybrid" if home is True else "onsite",
                salary_min=r.get("gehaltsspanneVon") if yearly else None,
                salary_max=r.get("gehaltsspanneBis") if yearly else None,
                contract_type={"UNBEFRISTET": "permanent", "BEFRISTET": "fixed-term"}.get(r.get("vertragsdauer") or ""),
                is_snippet=not text,
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
            s.name: s for s in (AdzunaSource(), ArbeitsagenturSource(), ArbeitnowSource(), GreenhouseSource(), LeverSource(), PersonioSource(),
                                RemotiveSource(), JobicySource(), HimalayasSource(), WeWorkRemotelySource(),
                                RemoteOkSource(), WorkingNomadsSource(), AshbySource(), WorkableSource(),
                                SmartRecruitersSource(), RecruiteeSource(), TeamtailorSource())
        }
        if get_settings().llm_provider == "fake":
            _registry = {"demo": FakeSource(demo_postings())}
            _registry["demo"].name = "demo"
    return _registry
