"""CV intake, posting ingestion, matching and writing checks."""

import pytest
from sqlalchemy import select

from app.connectors.sources import RawPosting
from app.models import CV, CVVersion, Match, Posting
from app.services import cvs as cv_service
from app.services import matching
from app.services import postings as posting_service
from app.services.campaigns import DEFAULT_WEIGHTS, CampaignConfig, get_config
from app.services.writing import detect_language, placeholder_problems, too_similar
from tests.helpers import CV_BACKEND, CV_LEAD, always_ok, campaign_of, make_world, raw


def test_cv_upload_parses_profile_and_versions(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        cv = s.execute(select(CV)).scalar_one()
        assert cv.is_default and cv.keywords == ["backend"]
        v1 = cv_service.current_version(s, cv)
        assert {x["name"] for x in v1.profile["skills"]} >= {"python", "kubernetes"}
        cv_id = cv.id
    r = client.post("/api/cvs", headers=w.headers, files={"file": ("cv2.txt", (CV_BACKEND + "\nNew line about Go.").encode(), "text/plain")},
                    data={"label": "Backend", "cv_id": str(cv_id)})
    assert r.status_code == 201
    with w.session() as s:
        assert len(s.execute(select(CVVersion)).scalars().all()) == 2
        assert len(s.execute(select(CV)).scalars().all()) == 1


def test_cv_limits_and_bad_files(client, llm):
    w = make_world(client, llm, cvs=[(f"CV{i}", CV_BACKEND + str(i), "") for i in range(4)])
    r = client.post("/api/cvs", headers=w.headers, files={"file": ("c.txt", CV_BACKEND.encode(), "text/plain")}, data={"label": "Fifth"})
    assert r.status_code == 400 and "up to 4" in r.json()["detail"]
    r = client.post("/api/cvs", headers=w.headers, files={"file": ("c.exe", b"MZ" * 100, "application/octet-stream")}, data={"label": "x"})
    assert r.status_code == 400
    r = client.post("/api/cvs", headers=w.headers, files={"file": ("c.pdf", b"not a pdf at all" * 10, "application/pdf")}, data={"label": "x"})
    assert r.status_code == 400
    r = client.post("/api/cvs", headers=w.headers, files={"file": ("c.txt", b"tiny", "text/plain")}, data={"label": "x"})
    assert r.status_code == 400


def test_ingest_dedupes_across_sources_and_validates_contact(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        p1, new1 = posting_service.ingest(s, llm, raw(source="adzuna"), mail_check=always_ok)
        p2, new2 = posting_service.ingest(s, llm, raw(source="arbeitnow", company="ACME"), mail_check=always_ok)
        assert new1 and not new2 and p1.id == p2.id
        assert p1.apply_route == "email" and p1.contact_email == "jobs@acme.at"
        noreply, _ = posting_service.ingest(s, llm, raw(title="Data Engineer", email="noreply@acme.at"), mail_check=always_ok)
        assert noreply.contact_email is None and noreply.apply_route == "form"
        dead, _ = posting_service.ingest(s, llm, raw(title="ML Engineer", email="jobs@dead.example"), mail_check=lambda d: False)
        assert dead.contact_email is None


def test_contact_must_appear_in_posting_text():
    text = "Apply to jobs@acme.at today"
    assert posting_service.validate_contact("jobs@acme.at", text, always_ok) == "jobs@acme.at"
    # An address the model produced that is not in the posting is refused (prompt-injection defence).
    assert posting_service.validate_contact("attacker@evil.example", text, always_ok) is None
    assert posting_service.validate_contact("privacy@acme.at", "write to privacy@acme.at", always_ok) is None
    assert posting_service.validate_contact("not-an-email", text, always_ok) is None


def test_company_normalisation():
    assert posting_service.normalize_company("ACME GmbH") == posting_service.normalize_company("Acme")
    assert posting_service.registered_domain("jobs.acme.co.at") == "acme.co.at"
    assert posting_service.registered_domain("mail.acme.at") == "acme.at"


def _posting(s, llm, **kw) -> Posting:
    return posting_service.ingest(s, llm, raw(**kw), mail_check=always_ok)[0]


def test_hard_filters(client, llm):
    from datetime import date

    w = make_world(client, llm, config={"salary_min": 50000, "countries": ["at"]})
    with w.session() as s:
        cfg = get_config(campaign_of(s, w))
        ok, fails, notes = matching.hard_filters(s, w.user_id, cfg, _posting(s, llm), {})
        assert ok and "salary not stated" in notes
        old = _posting(s, llm, title="Old Engineer", posted=date(2026, 9, 1))
        assert not matching.hard_filters(s, w.user_id, cfg, old, {})[0]
        low = _posting(s, llm, title="Low Engineer", extra="Salary: 30000")
        assert "salary is below your minimum" in matching.hard_filters(s, w.user_id, cfg, low, {})[1]
        strict = cfg.model_copy(update={"unknown_policy": {"salary": "skip", "work_mode": "apply", "age": "apply"}})
        assert "salary is not stated" in matching.hard_filters(s, w.user_id, strict, _posting(s, llm, title="X Engineer"), {})[1]
        de = _posting(s, llm, title="German Engineer", country="de", extra="On-site only", requirements="python")
        de.fields = {**de.fields, "work_mode": "onsite"}
        assert any("country" in f for f in matching.hard_filters(s, w.user_id, cfg, de, {})[1])
        sponsor = matching.hard_filters(s, w.user_id, cfg, _posting(s, llm, title="Visa Engineer"),
                                        {"work_authorisation": {"at": "needs_sponsorship"}})
        assert sponsor[0]  # remote posting: authorisation does not block


def test_block_list_filters(client, llm):
    w = make_world(client, llm)
    r = client.post("/api/blocks", headers=w.headers, json={"type": "company", "value": "Acme"})
    assert r.status_code == 201
    with w.session() as s:
        ok, fails, _ = matching.hard_filters(s, w.user_id, get_config(campaign_of(s, w)), _posting(s, llm), {})
        assert not ok and any("blocked" in f for f in fails)


def test_scoring_set_and_cv_choice(client, llm):
    w = make_world(client, llm, cvs=[("Backend", CV_BACKEND, "backend"), ("Lead", CV_LEAD, "team lead"),
                                      ("Third", CV_BACKEND + " extra words about banking", ""),
                                      ("Fourth", CV_LEAD + " extra words about retail", "")])
    with w.session() as s:
        posting = _posting(s, llm, title="Team Lead Engineering", requirements="leadership, hiring")
        labels = {cv.label for cv, _ in matching.scoring_set(s, w.user_id, posting)}
        assert "Lead" in labels          # keyword match is always scored
        assert "Backend" in labels       # the default CV is always scored
        assert len(labels) <= 4
        match = matching.evaluate(s, llm, campaign_of(s, w), get_config(campaign_of(s, w)), posting, {}, [])
        assert match.decision == "apply" and match.rubric["cv_label"] == "Lead" and match.total_score == 100


def test_choose_cv_rules():
    class C:
        def __init__(self, default=False):
            self.is_default = default

    a, b = {"cv": C(True), "total": 88.0, "keyword": False}, {"cv": C(), "total": 90.0, "keyword": False}
    assert matching.choose_cv([a, b], 85) is a                       # within 3 points: the default wins
    b2 = {"cv": C(), "total": 95.0, "keyword": False}
    assert matching.choose_cv([a, b2], 85) is b2                     # clear winner
    k = {"cv": C(), "total": 86.0, "keyword": True}
    assert matching.choose_cv([b2, k], 85) is k                      # exactly one keyword-matched CV that passes
    assert matching.choose_cv([{"cv": C(), "total": 70.0, "keyword": True}], 85) is None


def test_essential_missing_caps_score_and_threshold_skips(client, llm):
    rubric = {"dimensions": dict.fromkeys(DEFAULT_WEIGHTS, 100),
              "requirements": [{"requirement": "sap", "status": "missing", "essential": True}]}
    assert matching.totals(rubric, DEFAULT_WEIGHTS) == 70.0
    w = make_world(client, llm)
    with w.session() as s:
        posting = _posting(s, llm, title="SAP Engineer", requirements="sap, abap, python")
        match = matching.evaluate(s, llm, campaign_of(s, w), get_config(campaign_of(s, w)), posting, {}, [])
        assert match.decision == "skip" and "below your threshold" in match.reason
        again = matching.evaluate(s, llm, campaign_of(s, w), get_config(campaign_of(s, w)), posting, {}, [])
        assert again.id == match.id and len(s.execute(select(Match)).scalars().all()) == 1


def test_form_only_posting_becomes_manual(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        posting = _posting(s, llm, title="Form Engineer", email="")
        match = matching.evaluate(s, llm, campaign_of(s, w), get_config(campaign_of(s, w)), posting, {}, [])
        assert match.decision == "manual"


def test_writing_checks_units():
    assert placeholder_problems("Subject", "Dear [Name], hello", "Acme")
    assert placeholder_problems("S", "Dear,\nhello there", "Acme")
    assert not placeholder_problems("Application", "Dear Ms Huber, I am applying.", "Acme")
    assert detect_language("Sehr geehrte Damen und Herren, ich bewerbe mich mit der Bewerbung für die Stelle und das Team") == "de"
    assert detect_language("Dear team, I am applying for the role and I have the experience you need for it") == "en"
    assert too_similar("a b c d e f g h i j", ["a b c d e f g h i j"]) and not too_similar("completely different words here", ["a b c d e"])


def test_campaign_config_validation():
    with pytest.raises(ValueError, match="minutes"):
        CampaignConfig(daily_quota=30)                       # 240 minutes do not fit the 3-hour default window
    CampaignConfig(daily_quota=30, send_window={"start": "08:00", "end": "12:00"})
    CampaignConfig(daily_quota=20)
    with pytest.raises(ValueError):
        CampaignConfig(daily_quota=31, send_window={"start": "06:00", "end": "20:00"})
    with pytest.raises(ValueError):
        CampaignConfig(weights={**DEFAULT_WEIGHTS, "experience": 50})
    with pytest.raises(ValueError):
        CampaignConfig(send_window={"start": "12:00", "end": "09:00"})


def test_raw_posting_defaults():
    assert RawPosting(source="x", source_id="1", url="u", title="t", company="c").is_snippet is False


def test_a_draft_in_one_block_of_text_is_sent_back(llm):
    from app.models import Posting
    from app.services import writing
    from app.services.campaigns import CampaignConfig

    llm.on("fact_check", lambda p, s: {"claims": []})
    posting = Posting(title="Engineer", description="", fields={"language": "en"})
    words = " ".join(["I have built data platforms at Acme for years."] * 14)
    kw = dict(posting=posting, company_name="Acme", profile={}, fact_sheet={}, expected_language="en",
              previous_bodies=[], config=CampaignConfig())
    block = writing.run_checks(llm, {"subject": "Engineer - Alex", "body": f"Dear Acme team, {words} Kind regards, Alex"}, **kw)
    assert any("one block of text" in p for p in block["problems"])
    laid_out = f"Dear Acme team,\n\n{words}\n\nKind regards,\nAlex"
    assert writing.run_checks(llm, {"subject": "Engineer - Alex", "body": laid_out}, **kw)["passed"]


def test_an_excluded_country_fails_the_hard_filter_even_when_remote(client, llm):
    w = make_world(client, llm, config={"exclude_countries": ["pk"], "countries": []})
    with w.session() as s:
        cfg = get_config(campaign_of(s, w))
        pk = _posting(s, llm, title="Remote from Lahore", country="pk", extra="Fully remote")
        pk.fields = {**pk.fields, "work_mode": "remote"}
        ok, fails, _ = matching.hard_filters(s, w.user_id, cfg, pk, {})
        assert not ok and any("excluded" in f for f in fails)
        de = _posting(s, llm, title="Remote from Berlin", country="de", extra="Fully remote")
        assert matching.hard_filters(s, w.user_id, cfg, de, {})[0]
