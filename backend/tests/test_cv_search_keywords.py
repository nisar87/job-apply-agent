"""Searches built from the skills the user really has, and gaps shown only as suggestions."""

from sqlalchemy import select

from app.models import Campaign
from app.services import cvs as cv_service
from app.services import matching
from app.services import postings as posting_service
from app.services.campaigns import get_config
from tests.helpers import CV_BACKEND, CV_LEAD, always_ok, campaign_of, make_world, raw


def test_search_keywords_of_a_profile():
    # Terms read from the CV come first, then skills added later; duplicates are dropped.
    assert cv_service.search_keywords_of({"search_keywords": ["C#", "Python", "c#"], "skills": [{"name": "Go"}]}) \
        == ["C#", "Python", "Go"]
    # Skills are split into single terms and soft skills are left out. Terms are written as postings write
    # them: no vendor prefix, and a .NET technology also brings in the platform.
    profile = {"skills": [{"name": "C# and VB.NET"}, {"name": "Microsoft SQL Server"}, {"name": "Problem-Solving"},
                          {"name": "Git / GitHub"}, {"name": "TFS (Team Foundation Server)"}, {"name": "Leadership"}]}
    assert cv_service.search_keywords_of(profile) == ["C#", "VB.NET", ".NET", "SQL Server", "Git", "GitHub", "TFS"]
    assert len(cv_service.search_keywords_of({"search_keywords": [f"tech{i}" for i in range(30)]})) \
        == cv_service.MAX_SEARCH_KEYWORDS
    assert cv_service.search_keywords_of({}) == []


def test_user_search_keywords_come_from_active_cvs_default_first(client, llm):
    w = make_world(client, llm, cvs=(("Backend", CV_BACKEND, "backend"), ("Lead", CV_LEAD, "")))
    with w.session() as s:
        terms = cv_service.search_keywords(s, w.user_id)
    # The default CV's own keyword and skills come first; the second CV adds only what is new; soft skills are left out.
    assert terms[:6] == ["backend", "python", "postgresql", "kubernetes", "fastapi", "aws"]
    assert "hiring" in terms and "agile" in terms
    assert "leadership" not in terms and "stakeholder management" not in terms
    assert terms.count("python") == 1
    r = client.get("/api/cvs/search-keywords", headers=w.headers)
    assert r.status_code == 200 and r.json()["keywords"] == terms
    assert client.get("/api/cvs/search-keywords").status_code == 401


def test_a_search_without_keywords_uses_the_cv_skills(client, llm):
    w = make_world(client, llm)
    r = client.post("/api/campaigns", headers=w.headers, json={"name": "From my CV", "config": {"sources": ["fake"]}})
    assert r.status_code == 201, r.text
    assert r.json()["config"]["keywords"][:3] == ["backend", "python", "postgresql"]
    # Keywords the user gives are kept as they are.
    r = client.post("/api/campaigns", headers=w.headers,
                    json={"name": "Own words", "config": {"keywords": ["golang"], "sources": ["fake"], "daily_quota": 5}})
    assert r.status_code == 201, r.text
    assert r.json()["config"]["keywords"] == ["golang"]
    with w.session() as s:
        assert len(s.execute(select(Campaign)).scalars().all()) == 3


def test_setup_chat_without_roles_searches_for_the_cv_skills(client, llm):
    w = make_world(client, llm)
    llm.on("onboarding", lambda p, s: {
        "reply": "Got it: remote jobs in Austria.", "config": {"countries": ["at"], "work_modes": ["remote"]},
        "evidence": [{"setting": "countries", "user_words": "in Austria"}, {"setting": "work_modes", "user_words": "remote"}],
        "open_question": False})
    out = client.post("/api/setup/turn", headers=w.headers,
                      json={"history": [{"role": "user", "text": "Remote jobs in Austria please"}]}).json()
    assert out["keywords_from_cv"] is True and out["ready"] is True
    assert out["config"]["keywords"][:2] == ["backend", "python"]
    assert "match your CV" in out["reply"]
    # When the user names roles, those are used and the CV is not.
    llm.on("onboarding", lambda p, s: {
        "reply": "OK.", "config": {"keywords": ["data engineer"]},
        "evidence": [{"setting": "keywords", "user_words": "data engineer"}], "open_question": False})
    out = client.post("/api/setup/turn", headers=w.headers,
                      json={"history": [{"role": "user", "text": "data engineer roles"}]}).json()
    assert out["keywords_from_cv"] is False and out["config"]["keywords"] == ["data engineer"]


def test_keywords_match_whole_terms():
    from app.connectors.sources import matches_keywords

    assert not matches_keywords("Digital marketing lead", ["Git"])
    assert matches_keywords("Experience with Git and CI", ["Git"])
    assert not matches_keywords("Senior JavaScript developer", ["Java"])
    assert matches_keywords("ASP.NET Core developer", [".NET"])
    assert matches_keywords("Strong C# skills", ["C#"]) and not matches_keywords("ABC#123", ["C#"])
    assert matches_keywords("Microsoft SQL Server DBA", ["SQL Server"])
    assert matches_keywords("anything", []) and matches_keywords("anything", [" "])


def test_missing_requirements_are_suggestions_not_actions(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        posting = posting_service.ingest(s, llm, raw(title="SAP Engineer", requirements="sap, abap, python"),
                                         mail_check=always_ok)[0]
        campaign = campaign_of(s, w)
        match = matching.evaluate(s, llm, campaign, get_config(campaign), posting, {}, [])
        assert match.decision == "skip"
        assert match.rubric["suggestions"] == ["sap", "abap"]
        # The CV is left exactly as it was: suggestions never change the profile.
        assert "sap" not in cv_service.search_keywords(s, w.user_id)
    skipped = client.get("/api/matches?decision=skip", headers=w.headers).json()
    assert skipped[0]["rubric"]["suggestions"] == ["sap", "abap"]
    assert matching.suggestions({"requirements": [
        {"requirement": "go", "status": "partial"}, {"requirement": "rust", "status": "met"},
        {"requirement": "go", "status": "missing"}]}) == ["go"]
