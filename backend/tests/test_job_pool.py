"""The job harvester and the shared job pool: free boards are collected on their own schedule, company boards are
learned from pooled jobs and checked, and searches take matching jobs from the pool."""

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.connectors.sources import (
    ArbeitsagenturSource,
    FakeSource,
    LeverSource,
    RawPosting,
    RemotiveSource,
    SearchQuery,
    set_sources,
)
from app.db import system_session
from app.models import JobSite, PoolJob, Posting
from app.services import discovery, harvest, pool, sites
from app.services import postings as posting_service
from app.tasks.locks import get_redis
from tests.helpers import always_ok, campaign_of, make_world, raw

TODAY = datetime.now(UTC).date()


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def job(title, company="Acme GmbH", *, source="remotive", ref=None, country=None, mode=None, days_old=1,
        regions=None, location="", text=None) -> RawPosting:
    return RawPosting(source=source, source_id=ref or f"{company}-{title}", url=f"https://example.com/{ref or title}",
                      title=title, company=company, description=text if text is not None else f"{title} at {company}.",
                      location=location, country=country, work_mode=mode, hiring_regions=regions,
                      posted_date=TODAY - timedelta(days=days_old))


def test_the_pool_stores_jobs_once_refreshes_them_and_drops_old_ones(client):
    now = datetime.now(UTC)
    with system_session() as s:
        known = pool.known_keys(s)
        new = pool.store(s, [job("Python Engineer", ref="1", location="Berlin, Germany"), job("Data Engineer", ref="2"),
                             job("Python Engineer", ref="1")], known, now - timedelta(days=50))
        assert [r.source_id for r in new] == ["1", "2"]
        # Seen again later without its text (the harvester did not fetch it again): the text stays.
        again = job("Python Engineer", ref="1", text="")
        assert pool.store(s, [again], pool.known_keys(s), now) == []
        rows = {r.source_id: r for r in s.execute(select(PoolJob)).scalars()}
        assert len(rows) == 2 and rows["1"].description == "Python Engineer at Acme GmbH." and not rows["1"].is_snippet
        assert rows["1"].country == "de" and rows["1"].last_seen > rows["2"].last_seen
        assert pool.expire(s, now) == 1  # job 2 was last seen 50 days ago
        assert [r.source_id for r in s.execute(select(PoolJob)).scalars()] == ["1"]
    assert pool.guess_country("Wien") == "at" and pool.guess_country("Wilmington, DE") is None
    assert pool.guess_country("Remote - UK") == "gb" and pool.guess_country("Anywhere") is None


def test_the_harvester_collects_free_boards_learns_company_boards_and_checks_them(client, llm):
    w = make_world(client, llm, config={"keywords": ["engineer", "c#"], "countries": ["de", "at"]})
    asked = []
    yesterday = (TODAY - timedelta(days=1)).isoformat()

    def handler(request):
        asked.append(str(request.url))
        host, path = request.url.host, request.url.path
        if host == "remotive.com":
            return httpx.Response(200, json={"jobs": [
                {"id": 7, "title": "Backend Engineer", "company_name": "Acme", "candidate_required_location": "Europe",
                 "publication_date": yesterday, "url": "https://remotive.com/7",
                 "description": "<p>Apply on https://jobs.lever.co/acme/123</p>"},
                {"id": 8, "title": "Sales Lead", "company_name": "Beta", "candidate_required_location": "USA",
                 "publication_date": yesterday, "url": "https://remotive.com/8", "description": "Sell things"}]})
        if host == "rest.arbeitsagentur.de":
            if "/pc/v6/jobs" in path:
                assert request.url.params["size"] == "100" and request.url.params["veroeffentlichtseit"] == "14"
                return httpx.Response(200, json={"ergebnisliste": [
                    {"referenznummer": "10-A", "stellenangebotsTitel": f"Softwareentwickler {request.url.params['was']}",
                     "firma": "Muster GmbH", "datumErsteVeroeffentlichung": yesterday,
                     "stellenlokationen": [{"adresse": {"ort": "Berlin"}}]}]})
            ref = base64.b64decode(path.rsplit("/", 1)[1]).decode()
            return httpx.Response(200, json={"stellenangebotsBeschreibung": f"Volltext {ref}"})
        if host == "api.lever.co":
            assert path.endswith("/acme")
            return httpx.Response(200, json=[
                {"id": "l1", "text": "Platform Engineer", "descriptionPlain": "Kubernetes", "hostedUrl": "https://jobs.lever.co/acme/l1",
                 "createdAt": int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000),
                 "categories": {"location": "Vienna, Austria"}},
                {"id": "l2", "text": "Office Manager", "descriptionPlain": "Office", "hostedUrl": "https://jobs.lever.co/acme/l2",
                 "categories": {"location": "Vienna"}}])
        return httpx.Response(404)

    class Broken(FakeSource):
        def search(self, query):
            raise httpx.ConnectError("down")

    set_sources({"remotive": RemotiveSource(mock(handler)), "arbeitsagentur": ArbeitsagenturSource(mock(handler)),
                 "lever": LeverSource(mock(handler)), "jobicy": Broken()})
    calls_before = len(llm.calls)
    with system_session() as s:
        out = harvest.harvest(s)
    assert len(llm.calls) == calls_before  # no model call anywhere in the harvester
    assert out["sources"]["remotive"] == {"jobs": 2, "new": 2}
    assert out["sources"]["arbeitsagentur"]["new"] == 1  # the same job found by both terms counts once
    assert out["sources"]["lever"] == {"jobs": 2, "new": 2, "boards": 1}  # whole board, no keyword filter
    assert out["boards_learned"] == 1 and out["boards_checked"] == 1 and out["new"] == 5
    assert out["errors"] == ["jobicy: ConnectError"] and set(out["terms"]) == {"engineer", "c#"}
    with system_session() as s:
        rows = {(r.source, r.source_id): r for r in s.execute(select(PoolJob)).scalars()}
        board = s.execute(select(JobSite).where(JobSite.provider == "lever", JobSite.slug == "acme")).scalar_one()
    assert rows[("arbeitsagentur", "10-A")].description == "Volltext 10-A" and rows[("arbeitsagentur", "10-A")].country == "de"
    assert rows[("lever", "acme:l1")].country == "at" and board.last_checked_at is not None and board.last_matches == 1
    # The next run finds nothing new and leaves the board until it is due again; the text is not fetched again.
    details = sum("jobdetails" in u for u in asked)
    get_redis().flushdb()  # even without the feed cache
    with system_session() as s:
        again = harvest.harvest(s)
    assert again["new"] == 0 and again["boards_checked"] == 0
    assert sum("jobdetails" in u for u in asked) == details
    with w.session() as s:
        assert len(s.execute(select(Posting)).scalars().all()) == 0  # pooled jobs are not postings yet


def test_the_harvester_stops_at_its_time_budget(client, llm, monkeypatch):
    monkeypatch.setattr(harvest, "BOARD_CHUNK", 1)
    make_world(client, llm)
    set_sources({"lever": LeverSource(mock(lambda r: httpx.Response(200, json=[])))})
    with system_session() as s:
        for slug in ("a", "b", "c"):
            sites.learn(s, f"https://jobs.lever.co/{slug}/1")
    ticks = iter(range(0, 1000, 100))
    with system_session() as s:
        out = harvest.harvest(s, budget_seconds=250, clock=lambda: next(ticks))
    assert out["boards_checked"] < 3 and out["boards_left"] >= 1


def test_the_pool_source_keeps_what_fits_the_search(client, llm):
    w = make_world(client, llm)
    rows = [
        job("Python Engineer", ref="at", country="at", mode="onsite"),
        job("Remote Engineer US", ref="us-only", mode="remote", regions=["USA"]),
        job("Remote Engineer EU", ref="eu", mode="remote", regions=["Europe"]),
        job("Engineer Paris", ref="fr", country="fr", mode="onsite"),
        job("Old Engineer", ref="old", country="at", days_old=30),
        job("Accountant", ref="acc", country="at"),
        job("Engineer Read Before", ref="read", company="Seen AG", country="at", location="Vienna"),
    ]
    with w.session() as s:
        pool.store(s, rows, set())
        posting_service.ingest(s, llm, raw(title="Engineer Read Before", company="Seen AG", location="Vienna"),
                               mail_check=always_ok)
        source = pool.PoolSource()
        q = dict(keywords=["engineer"], countries=["at"], max_days_old=7, home_country="at")
        found = [r.source_id for r in source.search(SearchQuery(**q, remote_ok=True), session=s)]
        local = [r.source_id for r in source.search(SearchQuery(**q, remote_ok=False), session=s)]
        anywhere = {r.source_id for r in source.search(SearchQuery(keywords=["engineer"], country=""), session=s)}
        assert source.search(SearchQuery(**q)) == []  # no database session, nothing to read
    assert sorted(found) == ["at", "eu"]
    assert local == ["at"]
    assert anywhere == {"at", "us-only", "eu", "fr", "old"}


def test_the_pool_source_drops_what_the_hard_filters_would_reject_before_the_model_reads_it(client, llm):
    from app.models import Block, GlobalBlock

    w = make_world(client, llm)
    blocked_mail = job("Engineer Spam", ref="spam", country="at", mode="onsite")
    blocked_mail.contact_email = "jobs@spam.example"
    same_link = job("Engineer Same Link", ref="link", company="Linked AG", country="at")
    rows = [job("Engineer Hybrid", ref="hybrid", country="at", mode="hybrid"),
            job("Engineer Onsite", ref="onsite", country="at", mode="onsite"),
            job("Engineer Unknown Mode", ref="unknown", country="at"),
            job("Engineer Blocked", ref="blocked", company="Blocked GmbH", country="at"),
            blocked_mail, same_link]
    with w.session() as s:
        s.add(Block(user_id=w.user_id, type="company", value="Blocked"))
        s.add(GlobalBlock(type="domain", value="spam.example"))
        pool.store(s, rows, set())
        # Read before under another company name and place, but from the same link.
        read = raw(title="Engineer", company="Other", location="Graz")
        read.url = same_link.url
        posting_service.ingest(s, llm, read, mail_check=always_ok)
        calls = len(llm.calls)
        found = {r.source_id for r in pool.PoolSource().search(
            SearchQuery(keywords=["engineer"], countries=["at"], work_modes=["hybrid", "remote"]), session=s)}
        assert len(llm.calls) == calls  # no model call while choosing
    # Onsite is not wanted; an unstated mode is left to the agent, as the hard filters do.
    assert found == {"hybrid", "unknown"}


def test_every_chosen_country_is_searched(client, llm):
    w = make_world(client, llm)
    asked: dict[str, list] = {"websearch": [], "board": [], "pool": []}

    class Recorder(FakeSource):
        def search(self, query, **kw):
            asked[self.name].append(query.country)
            return []

    class Pool(pool.PoolSource):
        def search(self, query, session=None):
            asked["pool"].append(list(query.countries))
            return super().search(query, session=session)

    web, board = Recorder(), Recorder()
    web.name, web.per_country, board.name = "websearch", True, "board"
    set_sources({"websearch": web, "board": board, "pool": Pool()})
    with system_session() as s:
        pool.store(s, [job("Engineer Lahore", ref="pk", country="pk", mode="onsite"),
                       job("Engineer Munich", ref="de", country="de", mode="onsite"),
                       job("Engineer Lyon", ref="fr", country="fr", mode="onsite")], set())
    with w.session() as s:
        campaign = campaign_of(s, w)
        campaign.config = {**campaign.config, "countries": ["de", "pk"], "remote_outside_countries": False,
                           "sources": ["board"]}
        out = discovery.discover(s, llm, campaign, mail_check=always_ok)
        read = {p.source_id for p in s.execute(select(Posting)).scalars()}
    assert asked == {"websearch": ["de", "pk"], "board": ["de"], "pool": [["de", "pk"]]}
    assert read == {"pk", "de"} and out["new"] == 2  # both countries from the pool, nothing from elsewhere


def test_discovery_takes_jobs_from_the_pool_and_skips_boards_a_recent_harvest_covered(client, llm):
    w = make_world(client, llm)

    class NotAsked(FakeSource):
        def search(self, query):
            raise AssertionError("the pool covers this board")

    remotive = NotAsked()
    remotive.name = "remotive"
    set_sources({"pool": pool.PoolSource(), "remotive": remotive, "fake": FakeSource([])})
    with system_session() as s:
        pool.store(s, [raw(title="Backend Engineer", company="Pooled GmbH", source="remotive")], set())
    get_redis().set(harvest.LAST_KEY, json.dumps({"finished_at": datetime.now(UTC).isoformat(), "terms": ["engineer"],
                                                  "countries": ["at"], "sources": {"remotive": {"jobs": 1, "new": 1}}}))
    with w.session() as s:
        out = discovery.discover(s, llm, campaign_of(s, w), mail_check=always_ok)
        posting = s.execute(select(Posting)).scalar_one()
    assert out["new"] == 1 and out["left_to_pool"] == ["remotive"]
    assert (posting.source, posting.title) == ("remotive", "Backend Engineer")  # the original board is credited
    with w.session() as s:
        assert discovery.discover(s, llm, campaign_of(s, w), mail_check=always_ok)["found"] == 0  # already read
    # Without a recent harvest every live board is asked again.
    get_redis().delete(harvest.LAST_KEY)
    assert harvest.covered_sources(["engineer"]) == set()


def test_the_tick_starts_a_harvest_every_two_hours_and_the_operator_can_run_one(client, llm, monkeypatch):
    started = []
    monkeypatch.setattr(harvest, "_run_quietly", lambda: started.append(1))
    assert harvest.start_if_due() is True
    assert harvest.start_if_due() is False  # claimed for the next two hours
    monkeypatch.setenv("SCHEDULER_TOKEN", "t" * 40)
    from app.config import get_settings

    get_settings.cache_clear()
    set_sources({})
    r = client.post("/api/system/run/harvest", headers={"X-Scheduler-Token": "t" * 40})
    assert r.status_code == 200 and r.json()["result"]["new"] == 0
    w = make_world(client, llm)
    out = client.get("/api/job-pool", headers=w.headers).json()
    assert out["jobs"] == 0 and out["last_harvest"]["finished_at"]
    assert client.get("/api/job-pool").status_code == 401
