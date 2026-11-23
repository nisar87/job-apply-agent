"""What makes the agent useful every day: remote job boards, never applying where the employer cannot hire,
facts taken from the CV, and a notice when drafts are waiting."""

import httpx
from sqlalchemy import select

from app.agent import runs as agent_runs
from app.connectors.sources import HimalayasSource, JobicySource, RemotiveSource, SearchQuery, set_sources
from app.countries import hiring_allows, split_hiring_labels
from app.models import Notification
from app.services import cvs as cv_service
from app.services import matching
from app.services import postings as posting_service
from app.services.campaigns import CampaignConfig
from tests.helpers import always_ok, campaign_of, make_world, raw


def client_for(handler, calls):
    def counted(req):
        calls.append(str(req.url))
        return handler(req)
    return httpx.Client(transport=httpx.MockTransport(counted))


def test_hiring_labels_and_where_a_remote_job_can_hire():
    assert split_hiring_labels("Denmark,  Portugal | UK") == ["Denmark", "Portugal", "UK"]
    assert split_hiring_labels("USA Only") == ["USA"] and split_hiring_labels("") == []
    assert split_hiring_labels(["Europe (CET +/- 2h)", "Worldwide"]) == ["Europe", "Worldwide"]
    assert hiring_allows(["Worldwide"], "pk") is True and hiring_allows([], "pk") is True
    assert hiring_allows(["USA"], "pk") is False and hiring_allows(["United States", "Canada"], "pk") is False
    assert hiring_allows(["APAC"], "pk") is True and hiring_allows(["Asia"], "pk") is True
    assert hiring_allows(["Europe"], "pk") is False and hiring_allows(["Europe"], "de") is True
    assert hiring_allows(["Pakistan", "India"], "pk") is True
    assert hiring_allows(["UTC+3 to UTC+6"], "pk") is None                       # unclear, not refused


def test_remote_boards_parse_and_are_asked_at_most_once_per_window(client, llm):
    calls: list[str] = []

    def handler(req):
        host = req.url.host
        if host == "remotive.com":
            assert req.url.params["category"] == "software-dev"
            return httpx.Response(200, json={"jobs": [
                {"id": 1, "url": "https://remotive.com/1", "title": "Senior C# Developer", "company_name": "Acme",
                 "tags": ["c#", ".net"], "job_type": "full_time", "publication_date": "2026-11-14T10:00:00",
                 "candidate_required_location": "USA Only", "description": "<p>ASP.NET Core</p>"},
                {"id": 2, "url": "https://remotive.com/2", "title": "Designer", "company_name": "B", "tags": [],
                 "publication_date": "2026-11-14T10:00:00", "candidate_required_location": "Worldwide",
                 "description": "Figma"}]})
        if host == "jobicy.com":
            return httpx.Response(200, json={"jobs": [
                {"id": 7, "url": "https://jobicy.com/7", "jobTitle": ".NET Engineer", "companyName": "Beta",
                 "jobGeo": "Denmark,  Portugal", "jobType": ["full-time"], "pubDate": "2026-11-13 09:00:00",
                 "jobDescription": "<p>C# and SQL Server</p>"}]})
        if host == "himalayas.app":
            return httpx.Response(200, json={"jobs": [
                {"guid": "h1", "title": "Backend Engineer (.NET)", "companyName": "Gamma", "applicationLink": "https://gamma/apply",
                 "locationRestrictions": [], "description": "<p>C# APIs</p>", "pubDate": 1794600000,
                 "employmentType": "Full Time", "minSalary": 60000, "maxSalary": 80000}]})
        return httpx.Response(404)

    q = SearchQuery(keywords=["C#", ".NET"])
    remotive = RemotiveSource(client_for(handler, calls)).search(q)
    assert [p.title for p in remotive] == ["Senior C# Developer"] and remotive[0].hiring_regions == ["USA"]
    assert remotive[0].work_mode == "remote" and remotive[0].posted_date.isoformat() == "2026-11-14"
    jobicy = JobicySource(client_for(handler, calls)).search(q)
    assert jobicy[0].hiring_regions == ["Denmark", "Portugal"] and jobicy[0].contract_type == "full-time"
    hima = HimalayasSource(client_for(handler, calls)).search(q)
    assert hima[0].url == "https://gamma/apply" and hima[0].hiring_regions == ["Worldwide"] and hima[0].salary_min == 60000
    asked = len(calls)
    RemotiveSource(client_for(handler, calls)).search(q)          # a second search inside the window: from the cache
    JobicySource(client_for(handler, calls)).search(SearchQuery(keywords=["python"]))
    assert len(calls) == asked


def test_jobs_that_cannot_hire_from_the_users_country_are_skipped(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        cfg = CampaignConfig(keywords=["engineer"], countries=[])
        us_only = posting_service.ingest(s, llm, raw(title="US Engineer", company="U", country=None, work_mode="remote",
                                                     hiring_regions=["USA"]), mail_check=always_ok)[0]
        anywhere = posting_service.ingest(s, llm, raw(title="World Engineer", company="W", country=None, work_mode="remote",
                                                      hiring_regions=["Worldwide"]), mail_check=always_ok)[0]
        fuzzy = posting_service.ingest(s, llm, raw(title="TZ Engineer", company="T", country=None, work_mode="remote",
                                                   hiring_regions=["UTC+3 to UTC+6"]), mail_check=always_ok)[0]
        facts = {"home_country": "pk"}
        ok, fails, _ = matching.hard_filters(s, w.user_id, cfg, us_only, facts)
        assert not ok and fails == ["hires only from USA, not from Pakistan"]
        assert matching.hard_filters(s, w.user_id, cfg, anywhere, facts)[0]
        ok, _, notes = matching.hard_filters(s, w.user_id, cfg, fuzzy, facts)
        assert ok and any("unclear whether it hires from Pakistan" in n for n in notes)
        assert matching.hard_filters(s, w.user_id, cfg, us_only, {})[0]      # home country unknown: no guessing


def test_cv_facts_fill_only_empty_fact_sheet_fields(client, llm):
    w = make_world(client, llm)
    profile = {"country": "Pakistan", "location": "Islamabad", "phone": "+92 300 0000000",
               "languages": [{"name": "English", "level": "C1"}, {"name": "Urdu"}]}
    with w.session() as s:
        filled = cv_service.prefill_fact_sheet(s, w.user_id, profile)
        facts = cv_service.get_fact_sheet(s, w.user_id)
    assert set(filled) == {"home_country", "location", "phone", "languages", "right_to_work_countries"}
    assert facts["home_country"] == "pk" and facts["languages"] == "English C1, Urdu" and facts["notice_period"] == "1 month"
    with w.session() as s:
        cv_service.save_fact_sheet(s, w.user_id, {**facts, "location": "Lahore"})
        assert cv_service.prefill_fact_sheet(s, w.user_id, profile) == []            # nothing the user wrote is overwritten
        assert cv_service.get_fact_sheet(s, w.user_id)["location"] == "Lahore"
    r = client.put("/api/fact-sheet", headers=w.headers, json={**facts, "home_country": "Pakistan"})
    assert r.json()["home_country"] == "pk"


def test_a_run_that_leaves_drafts_tells_the_user(client, llm):
    w = make_world(client, llm)
    from app.connectors.sources import FakeSource

    set_sources({"fake": FakeSource([raw(title="Backend Engineer", company="Acme GmbH", email="jobs@acme.at")])})
    with w.session() as s:
        agent_runs.discovery_run(s, llm, campaign_of(s, w), mail_check=always_ok)
        agent_runs.prepare_run(s, llm, campaign_of(s, w))
        notes = s.execute(select(Notification).where(Notification.kind == "drafts_ready")).scalars().all()
    assert len(notes) == 1 and notes[0].link == "/drafts" and "ready for your approval" in notes[0].title
    assert "Backend Engineer" in notes[0].body
