"""The harvester's newer free sources: Hacker News "Who is hiring", Workable's public job search, Common Crawl's
URL index for finding company boards, and the back-off for boards without technical jobs. No network: every
request goes to a MockTransport handler."""

import json
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select

from app.connectors.sources import (
    CommonCrawlSource,
    HackerNewsSource,
    HarvestQuery,
    LeverSource,
    WorkableSearchSource,
    WorkableSource,
    set_sources,
)
from app.db import system_session
from app.models import JobSite, PoolJob
from app.services import contacts, harvest, sites
from app.tasks.locks import get_redis
from tests.helpers import make_world

NOW = datetime.now(UTC)


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _month_start(d: date, back: int = 0) -> date:
    first = d.replace(day=1)
    for _ in range(back):
        first = (first - timedelta(days=1)).replace(day=1)
    return first


def _hn_handler(asked: list, today: date):
    threads = [{"objectID": str(100 + back), "title": "Ask HN: Who is hiring? (some month)",
                "created_at": f"{_month_start(today, back).isoformat()}T15:00:00Z"} for back in range(3)]
    threads.append({"objectID": "999", "title": "Ask HN: Who wants to be hired?",
                    "created_at": f"{_month_start(today).isoformat()}T15:00:00Z"})
    comments = [
        {"id": 1, "created_at": f"{today.isoformat()}T09:00:00Z",
         "text": 'Acme Robotics (<a href="https:&#x2F;&#x2F;acme.io">https:&#x2F;&#x2F;acme.io</a>) | Senior Backend Engineer | '
                 "Berlin, Germany | REMOTE (EU) | €80k<p>We build robots in Python.<p>Apply: jobs [at] acme [dot] io"},
        {"id": 2, "created_at": f"{today.isoformat()}T10:00:00Z", "text": None, "deleted": True},
        {"id": 3, "created_at": f"{today.isoformat()}T11:00:00Z",
         "text": "Beta | ONSITE | Paris | Frontend Developer<p>React and TypeScript."},
        {"id": 4, "created_at": f"{today.isoformat()}T12:00:00Z", "text": "Just a question about the thread"},
    ]

    def handler(request):
        asked.append(str(request.url))
        if request.url.path.endswith("/search_by_date"):
            assert request.url.params["tags"] == "story,author_whoishiring"
            return httpx.Response(200, json={"hits": threads})
        item = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json={"id": int(item), "children": comments if item == "100" else []})

    return handler


def test_hacker_news_who_is_hiring_gives_jobs_with_addresses_to_apply_to():
    today = date.today()
    asked: list[str] = []
    mid_month = datetime(today.year, today.month, 15, tzinfo=UTC)
    out = HackerNewsSource(mock(_hn_handler(asked, today)), clock=lambda: mid_month).harvest(HarvestQuery())
    assert [p.source_id for p in out] == ["1", "3"]  # the deleted post and the plain reply are not jobs
    acme, beta = out
    assert (acme.company, acme.title, acme.location, acme.work_mode) == ("Acme Robotics", "Senior Backend Engineer",
                                                                         "Berlin, Germany", "remote")
    assert acme.contact_email == "jobs@acme.io" and acme.url == "https://news.ycombinator.com/item?id=1"
    assert "We build robots" in acme.description and acme.posted_date == today
    assert "acme.io" in contacts.candidate_sites(acme.company, acme.url, acme.description)
    assert (beta.company, beta.title, beta.location, beta.work_mode) == ("Beta", "Frontend Developer", "Paris", "onsite")
    assert sum("/items/" in u for u in asked) == 1  # only this month's thread
    # In the first days of a month last month's thread is read too; the answers are cached for six hours.
    early = datetime(today.year, today.month, 2, tzinfo=UTC)
    get_redis().flushdb()
    asked.clear()
    HackerNewsSource(mock(_hn_handler(asked, today)), clock=lambda: early).harvest(HarvestQuery())
    assert sorted(u.rsplit("/", 1)[1] for u in asked if "/items/" in u) == ["100", "101"]
    HackerNewsSource(mock(_hn_handler(asked, today)), clock=lambda: early).harvest(HarvestQuery())
    assert len(asked) == 3


def test_workable_search_pools_jobs_with_the_company_website_and_learns_their_boards(client, llm):
    make_world(client, llm, config={"keywords": ["python"], "countries": ["pk"]})
    yesterday = (NOW - timedelta(days=1)).isoformat()
    asked = []

    def handler(request):
        asked.append(request)
        p = request.url.params
        if request.url.host == "jobs.workable.com":
            assert p["query"] == "python"
            if p.get("workplace") == "remote":
                return httpx.Response(200, json={"jobs": [
                    {"id": "r1", "title": "Remote Python Developer", "created": yesterday, "workplace": "remote",
                     "location": {"countryName": "Germany", "countryCode": "DE"},
                     "company": {"title": "Delta GmbH", "url": "https://delta.example", "slug": "delta"}}]})
            assert p["location"] == "Pakistan"
            if p.get("pageToken") == "p2":
                return httpx.Response(200, json={"jobs": [
                    {"id": "old", "title": "Python Engineer", "created": (NOW - timedelta(days=40)).isoformat(),
                     "company": {"title": "Old Co"}}]})
            return httpx.Response(200, json={"nextPageToken": "p2", "jobs": [
                {"id": "w1", "title": "Python Engineer", "created": yesterday, "workplace": "on_site",
                 "url": "https://jobs.workable.com/view/w1", "description": "<p>Build APIs</p>",
                 "location": {"city": "Lahore", "countryName": "Pakistan", "countryCode": "PK"},
                 "company": {"title": "Gamma Ltd", "website": "https://gamma.example",
                             "url": "https://apply.workable.com/gamma/"}}]})
        if request.url.host == "apply.workable.com":
            slug = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"name": slug.title(), "jobs": [
                {"shortcode": "A1", "title": "Data Engineer", "url": f"https://apply.workable.com/{slug}/j/A1/",
                 "country": "Pakistan", "city": "Karachi", "published_on": yesterday}]})
        return httpx.Response(404)

    set_sources({"workablesearch": WorkableSearchSource(mock(handler)), "workable": WorkableSource(mock(handler))})
    with system_session() as s:
        out = harvest.harvest(s)
        rows = {r.source_id: r for r in s.execute(select(PoolJob)).scalars()}
        boards = {r.slug: r for r in s.execute(select(JobSite).where(JobSite.provider == "workable")).scalars()}
    assert out["sources"]["workablesearch"] == {"jobs": 2, "new": 2}  # the 40-day-old job is left out
    gamma = rows["w1"]
    assert (gamma.company, gamma.country, gamma.work_mode, gamma.location) == ("Gamma Ltd", "pk", "onsite", "Lahore, Pakistan")
    assert gamma.description.splitlines()[-1] == "Company website: https://gamma.example"
    assert "gamma.example" in contacts.candidate_sites(gamma.company, gamma.url, gamma.description)
    assert rows["r1"].work_mode == "remote" and rows["r1"].description.endswith("Company website: https://delta.example")
    # Both companies' Workable boards were learned and read in the same run.
    assert set(boards) == {"gamma", "delta"} and all(b.last_checked_at for b in boards.values())
    assert {"gamma:A1", "delta:A1"} <= set(rows) and out["boards_checked"] == 2


def test_common_crawl_finds_company_boards_a_slice_per_run(client, llm):
    make_world(client, llm)
    newest = {"id": "CC-MAIN-2026-44"}
    fetched = []
    pages = {"jobs.lever.co/*": 2, "boards.greenhouse.io/*": 1}
    lines = {("jobs.lever.co/*", "0"): ["https://jobs.lever.co/acme/1", "https://jobs.lever.co/acme/2"],
             ("jobs.lever.co/*", "1"): ["https://jobs.lever.co/beta/9"],
             ("boards.greenhouse.io/*", "0"): ["https://boards.greenhouse.io/gamma/jobs/5"]}

    def handler(request):
        if request.url.path == "/collinfo.json":
            return httpx.Response(200, json=[newest, {"id": "CC-MAIN-2026-40"}])
        assert request.url.path == f"/{newest['id']}-index"
        p = request.url.params
        if p.get("showNumPages") == "true":
            return httpx.Response(200, json={"pages": pages.get(p["url"], 0)})
        fetched.append((p["url"], p["page"]))
        body = "\n".join(json.dumps({"url": u}) for u in lines.get((p["url"], p["page"]), []))
        return httpx.Response(200, text=body)

    source = CommonCrawlSource(mock(handler))
    set_sources({"commoncrawl": source})
    with system_session() as s:
        out = harvest.harvest(s)
        learned = {(r.provider, r.slug) for r in s.execute(select(JobSite).where(JobSite.kind == "company")).scalars()}
    # Three steps per run: both Lever pages, then the first Greenhouse pattern's only page.
    assert learned == {("lever", "acme"), ("lever", "beta"), ("greenhouse", "gamma")}
    assert fetched == [("jobs.lever.co/*", "0"), ("jobs.lever.co/*", "1"), ("boards.greenhouse.io/*", "0")]
    assert out["sources"]["commoncrawl"] == {"links": 4, "boards": 3}
    assert source.cursor() == {"index": "CC-MAIN-2026-44", "pattern": 2, "page": 0, "pages": None}
    # The next runs carry on where the last one stopped (the remaining patterns have no pages).
    for _ in range(2):
        with system_session() as s:
            harvest.harvest(s)
    count = len(fetched)
    assert count == 3 and source.cursor()["pattern"] == len(CommonCrawlSource.PATTERNS)
    with system_session() as s:
        harvest.harvest(s)
    assert len(fetched) == count  # this index is done; nothing is read until a newer one appears
    newest["id"] = "CC-MAIN-2026-48"
    get_redis().delete("src:https://index.commoncrawl.org/collinfo.json?")
    with system_session() as s:
        harvest.harvest(s)
    assert source.cursor()["index"] == "CC-MAIN-2026-48" and len(fetched) == count + 3


def test_boards_without_technical_jobs_are_checked_less_often(client, llm):
    make_world(client, llm)
    titles = {"office": ["Office Manager"], "tech": ["Platform Engineer", "Office Manager"]}

    def handler(request):
        slug = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json=[{"id": f"{slug}{i}", "text": t, "hostedUrl": f"https://jobs.lever.co/{slug}/{i}"}
                                         for i, t in enumerate(titles[slug])])

    set_sources({"lever": LeverSource(mock(handler))})
    with system_session() as s:
        sites.learn(s, "https://jobs.lever.co/office/1")
        sites.learn(s, "https://jobs.lever.co/tech/1")
    at = NOW
    for _ in range(2):
        with system_session() as s:
            harvest.harvest(s, at)
        at += timedelta(hours=13)
    with system_session() as s:
        rows = {r.slug: r for r in s.execute(select(JobSite).where(JobSite.kind == "company")).scalars()}
        assert (rows["office"].idle_checks, rows["tech"].idle_checks) == (2, 0)
        due = {r.slug for r in sites.due_company_boards(s, None, at, limit=10)}
        assert due == {"tech"}  # the office-only board now waits three days
        later = {r.slug for r in sites.due_company_boards(s, None, at + timedelta(days=3), limit=10)}
        assert later == {"office", "tech"}
        # New boards and known ones take turns, so new ones cannot crowd out the rest.
        sites.learn(s, "https://jobs.lever.co/newone/1")
        assert [r.slug for r in sites.due_company_boards(s, None, at, limit=10, checked=False)] == ["newone"]
        assert [r.slug for r in sites.due_company_boards(s, None, at, limit=10, checked=True)] == ["tech"]
