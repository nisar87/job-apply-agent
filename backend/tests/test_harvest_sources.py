"""The harvester's newer free sources: Hacker News "Who is hiring", Workable's public job search, Common Crawl's
URL index for finding company boards, and the back-off for boards without technical jobs. No network: every
request goes to a MockTransport handler."""

import json
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select

from app.connectors.sources import (
    BerlinStartupJobsSource,
    CommonCrawlSource,
    HackerNewsSource,
    HarvestQuery,
    LandingJobsSource,
    LeverSource,
    RawPosting,
    SearchQuery,
    WaybackSource,
    WorkableSearchSource,
    WorkableSource,
    set_sources,
)
from app.db import system_session
from app.models import JobSite, PoolJob
from app.services import contacts, harvest, pool, sites
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
            if p["query"] in ("python visa sponsorship", "python relocation"):
                assert "location" not in p and "workplace" not in p  # anywhere in the world
                return httpx.Response(200, json={"jobs": []})
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
    asked_for = {r.url.params["query"] for r in asked if r.url.host == "jobs.workable.com"}
    assert asked_for == {"python", "python visa sponsorship", "python relocation"}


GERMAN = "Wir suchen einen Entwickler fuer unser Team und die Arbeit mit der Plattform ist sehr wichtig fuer uns und das Projekt."
ENGLISH = "We are looking for an engineer to join our team and you will work with the platform and the people in it."


def _pooled(title, ref, text, **kw):
    return RawPosting(source="remotive", source_id=ref, url=f"https://example.com/{ref}", title=title, company=f"Co {ref}",
                      description=text, posted_date=date.today() - timedelta(days=kw.pop("days_old", 1)), **kw)


def test_the_pool_leaves_out_jobs_in_languages_the_user_does_not_read(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        pool.store(s, [_pooled("Python Engineer", "de", GERMAN, country="at"),
                       _pooled("Python Engineer", "en", ENGLISH, country="at"),
                       _pooled("Python Engineer", "short", "Python, Kubernetes.", country="at")], set())
        q = dict(keywords=["engineer"], countries=["at"])
        english = {r.source_id for r in pool.PoolSource().search(SearchQuery(**q, languages=["en"]), session=s)}
        anything = {r.source_id for r in pool.PoolSource().search(SearchQuery(**q), session=s)}
    assert english == {"en", "short"}  # too short to tell: kept for the agent to judge
    assert anything == {"de", "en", "short"}


def test_the_pool_ranks_email_first_then_jobs_open_to_applicants_from_abroad(client, llm):
    def raw_(text="Build APIs.", **kw):
        return _pooled("Engineer", "x", text, **kw)

    assert pool.welcomes_abroad(raw_("We offer visa sponsorship and a relocation package."), "pk")
    assert pool.welcomes_abroad(raw_("Work from anywhere in the world."), "pk")
    assert pool.welcomes_abroad(raw_(work_mode="remote"), "pk")                      # remote, no limit stated
    assert pool.welcomes_abroad(raw_(work_mode="remote", hiring_regions=["Pakistan"]), "pk")
    assert not pool.welcomes_abroad(raw_(work_mode="remote", hiring_regions=["Europe"]), "pk")
    assert not pool.welcomes_abroad(raw_(country="de", work_mode="onsite"), "pk")
    w = make_world(client, llm)
    with w.session() as s:
        pool.store(s, [_pooled("Engineer Local New", "local", "Office in Vienna.", country="at", days_old=1),
                       _pooled("Engineer Visa Old", "visa", "We sponsor visas.", country="at", days_old=3),
                       _pooled("Engineer Visa New", "visa2", "Visa sponsorship available.", country="at", days_old=2),
                       _pooled("Engineer Mail Old", "mail", "Write to jobs@mail.example", country="at", days_old=5)], set())
        found = [r.source_id for r in pool.PoolSource().search(
            SearchQuery(keywords=["engineer"], countries=["at"], home_country="pk"), session=s)]
    assert found == ["mail", "visa2", "visa", "local"]


def test_landing_jobs_and_berlin_startup_jobs_feed_the_pool():
    old, new = (date.today() - timedelta(days=30)).isoformat(), (date.today() - timedelta(days=1)).isoformat()
    asked = []

    def handler(request):
        asked.append(str(request.url))
        if request.url.host == "landing.jobs":
            # The real shape (recorded 2026-11-17): no company name, the place in `locations`, ads published long
            # ago but refreshed while open, and an expiry date.
            offset = int(request.url.params["offset"])
            rows = [{"id": 1, "title": "Backend Engineer", "remote": False, "relocation_paid": True,
                     "locations": [{"city": "Lisbon", "country_code": "PT"}], "published_at": f"{old}T10:00:00Z",
                     "updated_at": f"{new}T10:00:00Z", "expires_at": "2099-01-01",
                     "url": "https://landing.jobs/at/lisbon-labs/backend-engineer",
                     "role_description": "<p>Ruby and Go.</p>", "gross_salary_low": 40000, "gross_salary_high": 60000}]
            rows += [{"id": 100 + i, "title": "Stale Job", "updated_at": f"{old}T10:00:00Z", "expires_at": "2099-01-01",
                      "url": "https://landing.jobs/at/old-co/stale-job"} for i in range(48)]
            rows += [{"id": 999, "title": "Expired Job", "updated_at": f"{new}T10:00:00Z", "expires_at": old,
                      "url": "https://landing.jobs/at/gone/expired-job"}]
            return httpx.Response(200, json=rows if offset == 0 else rows[1:3])
        return httpx.Response(200, text=(
            '<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>'
            "<title>Senior Go Developer // Kiez GmbH</title><link>https://berlinstartupjobs.com/engineering/go-dev/</link>"
            f"<pubDate>{date.today().strftime('%a, %d %b %Y')} 08:00:00 +0000</pubDate><guid>bsj-1</guid>"
            "<content:encoded>&lt;p&gt;English-speaking team. Relocation support.&lt;/p&gt;</content:encoded>"
            "</item></channel></rss>"))

    landing = LandingJobsSource(mock(handler)).harvest(HarvestQuery())
    # Not refreshed for a month, or past its expiry: left out. The list is not in date order, so every page is read
    # until a short one.
    assert [p.source_id for p in landing] == ["1"] and sum("offset=" in u for u in asked) == 2
    job = landing[0]
    assert (job.company, job.location, job.country, job.salary_min) == ("Lisbon Labs", "Lisbon, Portugal", "pt", 40000)
    assert job.posted_date == date.today() - timedelta(days=1)
    assert "relocation package" in job.description and pool.welcomes_abroad(job, "pk")
    bsj = BerlinStartupJobsSource(mock(handler)).harvest(HarvestQuery())
    assert [(p.title, p.company, p.country, p.posted_date) for p in bsj] == [("Senior Go Developer", "Kiez GmbH", "de", date.today())]
    assert "English-speaking team" in bsj[0].description


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
        assert p.get("showNumPages") is None  # the page count times out on a busy index; pages are read in turn
        fetched.append((p["url"], p["page"]))
        if int(p["page"]) >= pages.get(p["url"], 0):
            return httpx.Response(400, text="page out of range")  # what the index answers past the last page
        body = "\n".join(json.dumps({"url": u}) for u in lines.get((p["url"], p["page"]), []))
        return httpx.Response(200, text=body)

    source = CommonCrawlSource(mock(handler))
    set_sources({"commoncrawl": source})
    with system_session() as s:
        out = harvest.harvest(s)
        learned = {(r.provider, r.slug) for r in s.execute(select(JobSite).where(JobSite.kind == "company")).scalars()}
    # Three steps per run: both Lever pages, then the answer that there is no third one.
    assert learned == {("lever", "acme"), ("lever", "beta")}
    assert fetched == [("jobs.lever.co/*", "0"), ("jobs.lever.co/*", "1"), ("jobs.lever.co/*", "2")]
    assert out["sources"]["commoncrawl"] == {"links": 3, "boards": 2}
    assert source.cursor() == {"index": "CC-MAIN-2026-44", "pattern": 1, "page": 0, "pages": None}
    # The next runs carry on where the last one stopped: Greenhouse's page, then one look at each other pattern.
    for _ in range(3):
        with system_session() as s:
            harvest.harvest(s)
    count = len(fetched)
    assert count == 3 + 2 + 6 and source.cursor()["pattern"] == len(CommonCrawlSource.PATTERNS)
    with system_session() as s:
        assert ("greenhouse", "gamma") in {(r.provider, r.slug) for r in s.execute(select(JobSite)).scalars()}
        harvest.harvest(s)
    assert len(fetched) == count  # this index is done; nothing is read until a newer one appears
    newest["id"] = "CC-MAIN-2026-48"
    get_redis().delete("src:https://index.commoncrawl.org/collinfo.json?")
    with system_session() as s:
        harvest.harvest(s)
    assert source.cursor()["index"] == "CC-MAIN-2026-48" and len(fetched) == count + 3


def test_the_internet_archive_lists_company_boards_a_few_pages_per_run(client, llm):
    make_world(client, llm)
    asked = []
    pages = {  # (pattern, resume key) -> (archived addresses, next resume key)
        ("*.jobs.personio.de", None): (["https://acme.jobs.personio.de/", "http://beta.jobs.personio.de:80/"], "k1"),
        ("*.jobs.personio.de", "k1"): (["https://gamma.jobs.personio.de"], None),
        ("job-boards.greenhouse.io/*", None): (["https://job-boards.greenhouse.io/delta"], None),
    }

    def handler(request):
        p = request.url.params
        assert request.url.host == "web.archive.org" and p["filter"].startswith("original:^https?://")
        asked.append((p["url"], p.get("resumeKey")))
        lines, key = pages.get((p["url"], p.get("resumeKey")), ([], None))
        return httpx.Response(200, text="\n".join(lines + (["", key] if key else [])) + "\n")

    clock = [NOW]
    source = WaybackSource(mock(handler), clock=lambda: clock[0])
    set_sources({"wayback": source})
    with system_session() as s:
        out = harvest.harvest(s)
        learned = {(r.provider, r.slug) for r in s.execute(select(JobSite).where(JobSite.kind == "company")).scalars()}
    # Four pages a run: both Personio pages (the port is dropped), Greenhouse's, then the next pattern's first.
    assert {("personio", "acme"), ("personio", "beta"), ("personio", "gamma"), ("greenhouse", "delta")} <= learned
    assert asked[:3] == [("*.jobs.personio.de", None), ("*.jobs.personio.de", "k1"), ("job-boards.greenhouse.io/*", None)]
    assert len(asked) == 4 and out["sources"]["wayback"]["boards"] == 4
    for _ in range(2):  # the remaining patterns, four requests a run; then the pass is done
        with system_session() as s:
            harvest.harvest(s)
    every = len(WaybackSource.PATTERNS) + 1  # one request per pattern, two for Personio's two pages
    assert len(asked) == every and source.cursor().get("done_at")
    with system_session() as s:
        harvest.harvest(s)
    assert len(asked) == every  # nothing more until a month has passed
    clock[0] = NOW + timedelta(days=31)
    with system_session() as s:
        harvest.harvest(s)
    assert asked[-4] == ("*.jobs.personio.de", None)  # a new pass, for companies new to the archive


def test_company_boards_take_turns_by_hiring_system_and_keep_only_technical_jobs():
    class B:
        def __init__(self, provider, slug):
            self.provider, self.slug = provider, slug

    order = harvest._by_turns([B("lever", "a"), B("lever", "b"), B("lever", "c"), B("personio", "x"), B("ashby", "y")])
    assert [(b.provider, b.slug) for b in order] == [("lever", "a"), ("personio", "x"), ("ashby", "y"),
                                                     ("lever", "b"), ("lever", "c")]


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
