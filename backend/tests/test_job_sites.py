"""The directory of places to look: new boards, company careers pages learned from links, checked in turn,
paused when they keep failing."""

import httpx
from sqlalchemy import select

from app.connectors.sources import (
    AshbySource,
    BoardMissing,
    FakeSource,
    RemoteOkSource,
    SearchQuery,
    SmartRecruitersSource,
    WeWorkRemotelySource,
    set_sources,
)
from app.models import JobSite
from app.services import discovery, sites
from app.services import postings as posting_service
from tests.helpers import always_ok, campaign_of, make_world, raw


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_company_boards_are_found_in_links_and_text():
    found = sites.find_company_boards(
        "https://job-boards.greenhouse.io/GitLab/jobs/123",
        "Apply at https://jobs.lever.co/acme-corp/abc-def or https://jobs.ashbyhq.com/Linear/xyz/application, "
        "https://boards.greenhouse.io/embed/job_app?for=stripe&token=9, https://apply.workable.com/huggingface/j/AB12/ "
        "https://jobs.smartrecruiters.com/Ubisoft2/7444-dev https://beta.recruitee.com/o/dev "
        "https://zeta.jobs.personio.de/job/1 https://delta.teamtailor.com/jobs/5-dev https://api.lever.co/v0/x",
    )
    assert found == {("greenhouse", "gitlab"), ("lever", "acme-corp"), ("ashby", "Linear"), ("greenhouse", "stripe"),
                     ("workable", "huggingface"), ("smartrecruiters", "Ubisoft2"), ("recruitee", "beta"),
                     ("personio", "zeta"), ("teamtailor", "delta")}


def test_reading_a_posting_adds_the_employers_board_once(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        posting_service.ingest(s, llm, raw(title="Backend Engineer", company="Acme",
                                           extra="Apply: https://jobs.lever.co/acme/123"), mail_check=always_ok)
        posting_service.ingest(s, llm, raw(title="Data Engineer", company="Acme",
                                           extra="Apply: https://jobs.lever.co/acme/456"), mail_check=always_ok)
        rows = s.execute(select(JobSite)).scalars().all()
    assert [(r.provider, r.slug, r.kind, r.url) for r in rows] == [("lever", "acme", "company", "https://jobs.lever.co/acme")]


def test_discovery_checks_learned_boards_in_turn_and_pauses_dead_ones(client, llm):
    w = make_world(client, llm)

    class Board(FakeSource):
        def search(self, query):
            slug = query.company_slugs[0]
            if slug == "gone":
                raise BoardMissing(slug)
            if slug == "flaky":
                raise httpx.ConnectError("down")
            return [raw(title=f"Engineer at {slug}", company=slug, email=f"jobs@{slug}.at")]

    lever = Board()
    lever.name = "lever"
    set_sources({"lever": lever, "fake": FakeSource([])})
    with w.session() as s:
        for slug in ("good", "gone", "flaky"):
            sites.learn(s, f"https://jobs.lever.co/{slug}/1")
        out = discovery.discover(s, llm, campaign_of(s, w), mail_check=always_ok)
        rows = {r.slug: r for r in s.execute(select(JobSite).where(JobSite.kind == "company")).scalars()}
        board = s.execute(select(JobSite).where(JobSite.provider == "fake")).scalar_one()
    assert out["company_boards_checked"] == 3 and out["new"] == 1
    assert rows["good"].enabled and rows["good"].last_matches == 1 and rows["good"].failures == 0
    assert not rows["gone"].enabled and "no longer exists" in rows["gone"].last_error
    assert rows["flaky"].enabled and rows["flaky"].failures == 1
    assert board.kind == "board" and board.last_checked_at is not None
    # Checked boards wait their turn: an immediate second run checks none of them again.
    with w.session() as s:
        assert discovery.discover(s, llm, campaign_of(s, w), mail_check=always_ok)["company_boards_checked"] == 0
    # Five failures in a row pause a board.
    with w.session() as s:
        flaky = s.execute(select(JobSite).where(JobSite.slug == "flaky")).scalar_one()
        for _ in range(4):
            sites.record(flaky, ok=False, error="down")
        assert flaky.enabled is False and flaky.failures == 5


def test_new_board_sources_parse():
    def handler(req):
        if req.url.host == "weworkremotely.com":
            return httpx.Response(200, text=(
                "<rss><channel><item><title>Acme: Senior .NET Developer</title><region>Anywhere in the World</region>"
                "<type>Full-Time</type><description>&lt;p&gt;C# and SQL Server&lt;/p&gt;</description>"
                "<pubDate>Fri, 14 Nov 2026 10:00:00 +0000</pubDate><guid>g1</guid>"
                "<link>https://weworkremotely.com/remote-jobs/acme-net</link></item></channel></rss>"))
        if req.url.host == "remoteok.com":
            return httpx.Response(200, json=[{"legal": "Please link back"}, {
                "id": "9", "position": "C# Engineer", "company": "Beta", "tags": ["c#"], "description": "APIs",
                "location": "USA", "date": "2026-11-13T10:00:00+00:00", "url": "https://remoteok.com/9"}])
        if req.url.host == "api.ashbyhq.com":
            if req.url.path.endswith("/nobody"):
                return httpx.Response(404)
            return httpx.Response(200, json={"jobs": [{"id": "a1", "title": "Backend (.NET)", "isListed": True,
                                                       "isRemote": True, "descriptionPlain": "C#", "jobUrl": "https://jobs.ashbyhq.com/g/a1",
                                                       "publishedAt": "2026-11-12T00:00:00Z", "location": "Remote"}]})
        if req.url.host == "api.smartrecruiters.com":
            return httpx.Response(200, json={"totalFound": 0, "content": []})
        return httpx.Response(404)

    q = SearchQuery(keywords=["C#", ".NET"])
    wwr = WeWorkRemotelySource(mock(handler)).search(q)
    assert wwr[0].company == "Acme" and wwr[0].title == "Senior .NET Developer"
    assert wwr[0].hiring_regions == ["Anywhere in the World"] and wwr[0].posted_date.isoformat() == "2026-11-14"
    rok = RemoteOkSource(mock(handler)).search(q)
    assert len(rok) == 1 and rok[0].hiring_regions == ["USA"]
    ashby = AshbySource(mock(handler)).search(SearchQuery(keywords=[".NET"], company_slugs=["g"]))
    assert ashby[0].work_mode == "remote" and ashby[0].url == "https://jobs.ashbyhq.com/g/a1"
    for source, slug in ((AshbySource(mock(handler)), "nobody"), (SmartRecruitersSource(mock(handler)), "Nobody")):
        try:
            source.search(SearchQuery(keywords=["x"], company_slugs=[slug]))
            raise AssertionError("expected BoardMissing")
        except BoardMissing:
            pass


def test_sites_endpoint_lists_the_directory(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        sites.learn(s, "https://jobs.ashbyhq.com/linear/1")
        sites.board(s, "remotive")
    out = client.get("/api/job-sites", headers=w.headers).json()
    assert {(r["provider"], r["kind"]) for r in out} >= {("ashby", "company"), ("remotive", "board")}
    assert client.get("/api/job-sites").status_code == 401


def test_the_german_federal_job_board_gives_fresh_jobs_with_their_full_text(client):
    import base64

    from app.connectors.sources import ArbeitsagenturSource

    asked = []

    def handler(request):
        asked.append(request)
        assert request.headers["X-API-Key"] == "jobboerse-jobsuche"
        if "/pc/v6/jobs" in request.url.path:
            term = request.url.params["was"]
            assert request.url.params["veroeffentlichtseit"] == "5"
            rows = [{"referenznummer": "1-A-S", "stellenangebotsTitel": "Softwareentwickler C# (m/w/d)",
                     "firma": "Acme GmbH", "datumErsteVeroeffentlichung": "2026-11-14",
                     "stellenlokationen": [{"adresse": {"ort": "Berlin"}}], "verguetungsangabe": "JAHRESGEHALT",
                     "gehaltsspanneVon": 60000.0, "gehaltsspanneBis": 70000.0, "vertragsdauer": "UNBEFRISTET"},
                    {"referenznummer": "2-B-S", "stellenangebotsTitel": "Lagerhelfer", "firma": "Kisten AG",
                     "datumErsteVeroeffentlichung": "2026-11-15", "homeofficemoeglich": True}]
            return httpx.Response(200, json={"ergebnisliste": rows if term == "C#" else rows[:1]})
        ref = base64.b64decode(request.url.path.rsplit("/", 1)[1]).decode()
        text = {"1-A-S": "Wir entwickeln mit C# und .NET.", "2-B-S": "Kisten tragen."}[ref]
        return httpx.Response(200, json={"stellenangebotsBeschreibung": text})

    source = ArbeitsagenturSource(http=mock(handler))
    assert source.search(SearchQuery(keywords=["C#"], country="at")) == [] and not asked
    out = source.search(SearchQuery(keywords=["C#", ".NET"], country="de", max_days_old=5))
    assert len(out) == 1  # the warehouse job does not mention the keywords; the C# job came twice but counts once
    job = out[0]
    assert (job.source, job.source_id, job.company, job.location, job.country) == ("arbeitsagentur", "1-A-S", "Acme GmbH", "Berlin", "de")
    assert job.url == "https://www.arbeitsagentur.de/jobsuche/jobdetail/1-A-S" and job.description.startswith("Wir entwickeln")
    assert (job.salary_min, job.salary_max, job.contract_type, job.work_mode) == (60000.0, 70000.0, "permanent", "onsite")
    assert job.posted_date.isoformat() == "2026-11-14" and not job.is_snippet


def test_web_search_finds_jobs_like_a_chat_assistant_and_drops_made_up_links(client, llm):
    from app.connectors.sources import WebSearchSource, registered_site

    asked = []

    def answer(prompt, _):
        asked.append(prompt)
        if "fully remote" in prompt:
            return ("Remote C# Engineer | Acme | Remote | 2026-11-15 | https://jobs.acme.io/42\n", [{"title": "acme.io"}])
        return ("Here you go:\n"
                "Softwareentwickler C# | Beispiel GmbH | Berlin | November 15, 2026 | https://www.stepstone.de/job/1).\n"
                "Senior .NET Developer | Ghost AG | Hamburg | 2026-11-14 | https://ghost-careers.example/9\n"
                "Not a job line\n"
                "Search | Google | - | - | https://www.google.com/search?q=x\n",
                [{"title": "stepstone.de"}, {"title": "google.com"}])

    llm.on("web_search", answer)
    q = SearchQuery(keywords=["C#", ".NET"], country="de", max_days_old=7, home_country="pk", remote_ok=True,
                    work_modes=["remote", "hybrid", "onsite"])
    out = WebSearchSource().search(q, llm=llm)
    assert [(p.title, p.url, p.country, p.work_mode) for p in out] == [
        ("Softwareentwickler C#", "https://www.stepstone.de/job/1", "de", None),
        ("Remote C# Engineer", "https://jobs.acme.io/42", None, "remote"),
    ]  # the Ghost AG link was not among the pages Google returned; Google's own page is not a posting
    assert out[0].posted_date.isoformat() == "2026-11-15" and out[0].is_snippet
    assert len(asked) == 3 and "Germany" in asked[0] and "visa sponsorship" in asked[1] and "Pakistan" in asked[2]
    # The same day the answers are reused: no second round of searches.
    WebSearchSource().search(q, llm=llm)
    assert len(asked) == 3
    assert WebSearchSource().search(q) == []  # no model, nothing to search with
    assert registered_site("https://a.b.example.co.uk/x") == "example.co.uk" and registered_site("stepstone.de") == "stepstone.de"
    assert WebSearchSource.is_posting_link("https://www.rozee.pk/acme-net-developer-jobs-1234")
    assert WebSearchSource.is_posting_link("https://careers.trivago.com/job/software-engineer/")
    assert not WebSearchSource.is_posting_link("https://pk.indeed.com/")
    assert not WebSearchSource.is_posting_link("https://apply.workable.com/covergo/")
