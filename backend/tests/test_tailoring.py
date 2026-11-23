"""CV tailoring client against a simulated AI CV Studio service (docs/CV_TAILORING_API.md)."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app import storage
from app.config import get_settings
from app.models import CVVersion, User
from app.services import postings as posting_service
from app.services import tailoring
from tests.helpers import always_ok, make_world, raw

PDF = b"%PDF-1.4 tailored"
RUBRIC = {"requirements": [
    {"requirement": "SQL Server", "status": "met", "essential": True, "evidence": "Ran large database audits"},
    {"requirement": "Python", "status": "met", "essential": True, "evidence": "Python services"},          # CV already says it
    {"requirement": "Kubernetes", "status": "missing", "essential": True, "evidence": ""},                  # not proven
    {"requirement": "Docker", "status": "met", "essential": False, "evidence": "Ran large database audits"},  # not essential
    {"requirement": "Terraform", "status": "met", "essential": True, "evidence": "Invented line"},          # evidence not in CV
]}
CV_TEXT = "Ran large database audits. Built Python services."


def test_only_essential_proven_terms_with_real_evidence_are_emphasised():
    assert tailoring.emphasis_for(RUBRIC, CV_TEXT) == [{"term": "SQL Server", "evidence": "Ran large database audits"}]


class Service:
    """A stand-in for the service: records calls and answers as scripted."""

    def __init__(self, *, processing_polls=0, status=200, error=None, text="Ran large database audits"):
        self.calls, self.processing_polls, self.status, self.error, self.text = [], processing_polls, status, error, text
        self.polls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get("idempotency-key")))
        assert request.headers["authorization"] == "Bearer key"
        if request.method == "POST":
            if self.error:
                return httpx.Response(self.status, json={"error": {"code": self.error, "message": "no"}})
            body = json.loads(request.content)
            assert body["user_ref"].startswith("u_") and "@" not in body["user_ref"]
            if self.processing_polls:
                return httpx.Response(202, json={"id": "tlr_1", "status": "processing"})
            return httpx.Response(200, json=self.done())
        if request.url.path.endswith("/pdf"):
            return httpx.Response(200, content=PDF)
        if request.method == "DELETE":
            return httpx.Response(200, json={"deleted": 3})
        self.polls += 1
        return httpx.Response(200, json=self.done() if self.polls >= self.processing_polls else {"id": "tlr_1", "status": "processing"})

    def done(self):
        return {"id": "tlr_1", "status": "completed", "template": "plain", "pages": 2, "pdf_path": "/v1/tailor/tlr_1/pdf",
                "text": self.text, "changes": [{"kind": "reordered", "where": "Skills", "detail": "x"}], "usage": {"cost_usd": 0.01}}

    def client(self):
        return tailoring.TailorClient("https://tailor.example/v1", "key", httpx.Client(transport=httpx.MockTransport(self.handler)),
                                      sleep=lambda s: None)


def test_client_waits_for_a_slow_result_and_downloads_the_pdf():
    svc = Service(processing_polls=2)
    c = svc.client()
    result = c.tailor({"user_ref": "u_1"}, "k1")
    assert result["status"] == "completed" and svc.polls == 2 and c.pdf(result) == PDF
    assert svc.calls[0][2] == "k1"


def test_client_reports_the_services_error_codes_and_refuses_plain_http():
    with pytest.raises(tailoring.TailorError) as e:
        Service(status=429, error="quota_exhausted").client().tailor({"user_ref": "u_1"}, "k")
    assert e.value.code == "quota_exhausted"
    with pytest.raises(tailoring.TailorError):
        tailoring.TailorClient("http://tailor.example/v1", "key")


class Llm:
    def __init__(self, unsupported=()):
        self.unsupported = list(unsupported)

    def generate_structured(self, prompt, schema, **kw):
        return SimpleNamespace(data={"claims": [{"claim": c, "supported": False} for c in self.unsupported]})


@contextmanager
def job(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        user = s.get(User, w.user_id)
        version = s.execute(select(CVVersion).where(CVVersion.user_id == w.user_id)).scalars().first()
        version.extracted_text = CV_TEXT
        posting = posting_service.ingest(s, llm, raw(title="DBA", company="Acme GmbH"), mail_check=always_ok)[0]
        match = SimpleNamespace(decision="apply", total_score=90.0, rubric=RUBRIC)
        yield s, user, version, posting, match


def turn_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "cv_tailor_url", "https://tailor.example/v1")
    monkeypatch.setattr(get_settings(), "cv_tailor_api_key", "key")
    monkeypatch.setattr(tailoring, "_paused_until", 0.0)
    monkeypatch.setattr(get_settings(), "cv_tailor_min_terms", 1)


def test_a_tailored_cv_is_stored_with_its_changes_and_one_request(client, llm, monkeypatch):
    turn_on(monkeypatch)
    with job(client, llm) as (s, user, version, posting, match):
        svc = Service()
        att = tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                            purpose="email", client=svc.client())
        assert att and att["tailored"]["template"] == "plain" and storage.load(att["key"]) == PDF
        posts = [c for c in svc.calls if c[0] == "POST"]
        assert len(posts) == 1 and posts[0][2].startswith("t1-")


def test_a_cv_that_states_something_new_is_discarded(client, llm, monkeypatch):
    turn_on(monkeypatch)
    with job(client, llm) as (s, user, version, posting, match):
        att = tailoring.tailored_attachment(s, Llm(["Led a team of 40"]), user=user, posting=posting, match=match, version=version,
                                            purpose="email", client=Service(text="Led a team of 40").client())
        assert att is None


def test_every_failure_means_the_original_cv(client, llm, monkeypatch):
    turn_on(monkeypatch)
    with job(client, llm) as (s, user, version, posting, match):
        for status, code in ((429, "quota_exhausted"), (503, "unavailable"), (422, "could_not_tailor_faithfully")):
            assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                                 purpose="upload", client=Service(status=status, error=code).client()) is None


def test_it_is_off_without_an_address_and_below_the_score(client, llm, monkeypatch):
    with job(client, llm) as (s, user, version, posting, match):
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version, purpose="email") is None
        turn_on(monkeypatch)
        match.total_score = 80.0
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                             purpose="email", client=Service().client()) is None


def test_deleting_an_account_asks_the_service_to_forget_the_person(monkeypatch):
    turn_on(monkeypatch)
    import uuid

    svc = Service()
    tailoring.delete_remote_data(uuid.uuid4(), client=svc.client())
    assert svc.calls[0][0] == "DELETE" and "/users/u_" in svc.calls[0][1]


def test_quota_exhausted_pauses_the_service_and_a_non_english_job_is_not_sent(client, llm, monkeypatch):
    turn_on(monkeypatch)
    with job(client, llm) as (s, user, version, posting, match):
        posting.fields = {**(posting.fields or {}), "language": "de"}
        svc = Service()
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                             purpose="email", client=svc.client()) is None
        assert not [c for c in svc.calls if c[0] == "POST"]            # English only: never asked
        posting.fields = {**posting.fields, "language": "en"}
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                             purpose="email", client=Service(status=429, error="quota_exhausted").client()) is None
        assert tailoring.should_tailor(match, [{"term": "x", "evidence": "y"}]) is False   # paused until the budget resets


def test_the_request_key_changes_when_the_request_changes(client, llm, monkeypatch):
    turn_on(monkeypatch)
    with job(client, llm) as (s, user, version, posting, match):
        keys = []
        for purpose in ("email", "upload"):
            svc = Service()
            tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                          purpose=purpose, client=svc.client())
            keys += [c[2] for c in svc.calls if c[0] == "POST"]
        assert len(keys) == 2 and keys[0] != keys[1] and all(len(k) <= 100 for k in keys)


def _two_terms(match):
    rubric = {"requirements": RUBRIC["requirements"] + [
        {"requirement": "Index tuning", "status": "met", "essential": True, "evidence": "Built Python services"}]}
    match.rubric = rubric


def test_one_reworded_term_is_not_worth_a_call_but_two_are(client, llm, monkeypatch):
    turn_on(monkeypatch)
    monkeypatch.setattr(get_settings(), "cv_tailor_min_terms", 2)
    with job(client, llm) as (s, user, version, posting, match):
        svc = Service()
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                             purpose="email", client=svc.client()) is None
        assert not svc.calls
        _two_terms(match)
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                             purpose="email", client=svc.client()) is not None


def test_a_cv_made_for_more_terms_serves_a_job_needing_fewer(client, llm, monkeypatch):
    turn_on(monkeypatch)
    with job(client, llm) as (s, user, version, posting, match):
        from app.models import Application, Campaign
        campaign = s.execute(select(Campaign).where(Campaign.user_id == user.id)).scalars().first()
        _two_terms(match)
        first = Service()
        att = tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                            purpose="email", client=first.client())
        s.add(Application(user_id=user.id, posting_id=posting.id, campaign_id=campaign.id, cv_version_id=version.id, status="submitted", route="email",
                          idempotency_key="k", subject="", body="", attachments=[att]))
        s.flush()
        match.rubric = RUBRIC                       # the next job needs only one of those terms
        second = Service()
        again = tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                              purpose="email", client=second.client())
        assert again == att and not second.calls


def test_a_refusal_that_will_not_change_is_not_asked_again_but_a_busy_service_is(client, llm, monkeypatch):
    turn_on(monkeypatch)
    with job(client, llm) as (s, user, version, posting, match):
        refuse = Service(status=422, error="could_not_tailor_faithfully")
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                             purpose="email", client=refuse.client()) is None
        retry = Service()
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                             purpose="email", client=retry.client()) is None
        assert not retry.calls                          # same kind of job, same terms: not asked again
        busy = Service(status=503, error="unavailable")
        monkeypatch.setattr(tailoring, "_refused_before", lambda *a: False)
        tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                      purpose="upload", client=busy.client())
        assert busy.calls


def test_a_person_has_a_daily_limit_on_calls(client, llm, monkeypatch):
    turn_on(monkeypatch)
    monkeypatch.setattr(get_settings(), "cv_tailor_daily_per_user", 1)
    with job(client, llm) as (s, user, version, posting, match):
        busy = Service(status=503, error="unavailable")
        tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                      purpose="email", client=busy.client())
        again = Service()
        assert tailoring.tailored_attachment(s, Llm(), user=user, posting=posting, match=match, version=version,
                                             purpose="upload", client=again.client()) is None
        assert not again.calls


def test_the_library_picks_the_closest_saved_cv_and_asks_the_service_only_when_none_fits(client, llm, monkeypatch):
    turn_on(monkeypatch)
    with job(client, llm) as (s, user, version, posting, match):
        from app.models import Application, Campaign
        campaign = s.execute(select(Campaign).where(Campaign.user_id == user.id)).scalars().first()

        def tailored(terms, key):
            return {"name": "x.pdf", "key": key, "tailored": {"purpose": "upload", "terms": terms}}
        wide = tailored(["Microsoft SQL Server", "Python", "Docker", "Kubernetes"], "wide")
        close = tailored(["SQL Server", "Python", "Docker"], "close")
        s.add(Application(user_id=user.id, posting_id=posting.id, campaign_id=campaign.id, cv_version_id=version.id,
                          status="submitted", route="form", idempotency_key="k", subject="", body="", attachments=[wide, close]))
        s.flush()
        match_for = lambda *terms: [{"term": t, "evidence": "e"} for t in terms]    # noqa: E731
        # wording differs ("SQL Server" vs "Microsoft SQL Server"); the closest saved CV wins, an upload CV serves an email
        assert tailoring.library_match(s, user.id, {"sql server", "python", "docker"}, "email") == close
        assert tailoring.library_match(s, user.id, {"sql server", "python", "docker", "kubernetes"}, "email") == wide
        # three of four needed keywords is enough (0.75); two of four is not
        assert tailoring.library_match(s, user.id, {"sql server", "python", "docker", "terraform"}, "email") in (close, wide)
        assert tailoring.library_match(s, user.id, {"sql server", "go", "rust", "terraform"}, "email") is None
        assert match_for("a")
