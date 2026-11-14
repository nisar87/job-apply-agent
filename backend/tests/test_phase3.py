"""Web application forms: planning answers, stop conditions, review before submit, and a real browser."""

import http.server
import os
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent import runs as agent_runs
from app.config import get_settings
from app.connectors.browser import Blocker, FakeDriver, FormField, set_driver_factory
from app.models import Application, AuditLog, Campaign, Posting, User
from app.services import forms, rules
from app.services import postings as posting_service
from app.services.campaigns import CampaignConfig
from tests.helpers import NOW, always_ok, campaign_of, make_world, raw
from tests.test_phase1_agent import ctx_for

FIX = Path(__file__).parent / "fixtures"

STANDARD = [
    FormField("f0", "First name", "text", True), FormField("f1", "Last name", "text", True),
    FormField("f2", "Email", "email", True), FormField("f3", "Phone", "tel", False),
    FormField("f4", "Resume/CV", "file", True), FormField("f5", "Cover letter", "textarea", False),
    FormField("f6", "Notice period", "text", True),
    FormField("f7", "Are you legally authorized to work in Austria?", "radio", True, ["Yes", "No"]),
    FormField("f8", "Gender", "select", False, ["Female", "Male", "Prefer not to say"]),
    FormField("f9", "I agree to the privacy policy", "checkbox", True),
]


def form_app(client, llm, url="https://boards.greenhouse.io/donau/jobs/1", approval_mode="review", **kw):
    """A user with one form-only application on the manual-apply list."""
    from app.agent.tools import draft_application, score_job

    w = make_world(client, llm, approval_mode=approval_mode, **kw)
    with w.session() as s:
        r = raw(title="Cloud Engineer", company="Donau SE", email="", requirements="python, aws")
        r.url = url
        posting = posting_service.ingest(s, llm, r, mail_check=always_ok)[0]
        ctx = ctx_for(s, w, llm)
        score_job(ctx, str(posting.id))
        out = draft_application(ctx, str(posting.id))
        assert out["status"] == "manual_apply"
        w.app_id = __import__("uuid").UUID(out["application_id"])
    return w


def test_hiring_system_detection():
    assert forms.hiring_system("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert forms.hiring_system("https://jobs.lever.co/acme/abc") == "lever"
    assert forms.hiring_system("https://acme.jobs.personio.de/job/5") == "personio"
    assert forms.hiring_system("https://careers.acme.at/apply") == "generic"
    assert forms.hiring_system("https://evilgreenhouse.io.attacker.example/x") == "generic"


def test_plan_answers_from_profile_and_fact_sheet(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        user = s.get(User, w.user_id)
        profile = {"name": "Alex Example", "email": "alex@example.com", "links": ["https://linkedin.com/in/alex"]}
        facts = {"notice_period": "1 month", "work_authorisation": {"at": "citizen"}, "phone": "+43 660 1234567"}
        plan = forms.plan_answers(llm, STANDARD, user=user, profile=profile, facts=facts, config=CampaignConfig(), country="at",
                                  cover_text="My cover letter")
        assert plan["missing"] == []
        assert plan["answers"] == {"f0": "Alex", "f1": "Example", "f2": "alex@example.com", "f3": "+43 660 1234567",
                                   "f5": "My cover letter", "f6": "1 month", "f7": "Yes", "f8": "Prefer not to say", "f9": True}
        assert plan["files"] == {"f4": "cv"} and plan["consents"] == ["I agree to the privacy policy"]


def test_unknown_required_question_stops_and_salary_needs_permission(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        user = s.get(User, w.user_id)
        base = {"user": user, "profile": {"name": "Alex Example"}, "country": "at", "cover_text": "x"}
        odd = [FormField("q", "Describe your experience with quantum error correction", "textarea", True)]
        assert forms.plan_answers(llm, odd, facts={}, config=CampaignConfig(), **base)["missing"] == [odd[0].label]
        llm.on("form_answer", lambda p, sch: {"answer": "Five years", "fully_covered": True})
        assert forms.plan_answers(llm, odd, facts={}, config=CampaignConfig(), **base)["answers"] == {"q": "Five years"}
        salary = [FormField("s", "Salary expectation", "text", True)]
        facts = {"salary_expectation": "65000"}
        assert forms.plan_answers(llm, salary, facts=facts, config=CampaignConfig(), **base)["missing"] == ["Salary expectation"]
        assert forms.plan_answers(llm, salary, facts=facts, config=CampaignConfig(may_state_salary=True), **base)["answers"] == {"s": "65000"}
        # Authorisation unknown and required: never guessed.
        auth = [FormField("a", "Do you require visa sponsorship?", "radio", True, ["Yes", "No"])]
        llm.on("form_answer", lambda p, sch: {"answer": "", "fully_covered": False})
        assert forms.plan_answers(llm, auth, facts={}, config=CampaignConfig(), **base)["missing"]
        gender = [FormField("g", "Gender", "select", True, ["Female", "Male"])]
        assert forms.plan_answers(llm, gender, facts={}, config=CampaignConfig(), **base)["missing"] == ["Gender"]


def test_prepare_then_approve_then_submit_with_fake_browser(client, llm):
    w = form_app(client, llm)
    driver = FakeDriver(list(STANDARD), success_text="Thank you for applying to Donau SE")
    set_driver_factory(lambda: driver)
    r = client.post(f"/api/applications/{w.app_id}/form/prepare", headers=w.headers)
    assert r.status_code == 200 and r.json()["status"] == "form_ready"
    assert not driver.submitted and driver.uploads == {"f4": "cv.txt"} and driver.filled["f6"] == "1 month"
    review = client.get(f"/api/applications/{w.app_id}/form", headers=w.headers).json()
    assert review["hiring_system"] == "greenhouse" and review["consents"] == ["I agree to the privacy policy"]
    assert {"label": "Notice period", "value": "1 month"} in review["answers"] and review["has_screenshot"]
    assert client.get(f"/api/applications/{w.app_id}/form/screenshot", headers=w.headers).content.startswith(b"\x89PNG")
    with w.session() as s:                                  # the scheduler does not submit an unapproved form
        out = agent_runs.dispatch_run(s, w.user_id, NOW)
        assert out["sent"] == [] and out["refused"][str(w.app_id)] == "needs_approval"
    assert not driver.submitted
    with w.session() as s:
        app = s.get(Application, w.app_id)
        forms.approve_form(s, app, NOW)
        forms.submit_form(s, app, now=NOW, driver=driver)
        assert app.status == "sent" and app.sent_at == NOW and app.form_data["proof_key"]
        assert s.execute(select(AuditLog).where(AuditLog.tool == "submit_form")).scalar_one().result["submitted"] is True
    assert driver.submitted
    set_driver_factory(None)


@pytest.mark.parametrize("blocker,text", [
    (Blocker("captcha", "the page shows a CAPTCHA"), "CAPTCHA"),
    (Blocker("login", "the page asks for a password"), "password"),
    (Blocker("payment", "the page asks for payment details"), "payment"),
])
def test_stop_conditions_leave_the_job_on_the_manual_list(client, llm, blocker, text):
    w = form_app(client, llm)
    set_driver_factory(lambda: FakeDriver(list(STANDARD), blockers=[blocker]))
    r = client.post(f"/api/applications/{w.app_id}/form/prepare", headers=w.headers)
    assert r.status_code == 409 and text in r.json()["detail"]
    app = client.get(f"/api/applications/{w.app_id}", headers=w.headers).json()["application"]
    assert app["status"] == "manual_apply" and "Apply by hand" in app["skip_reason"]
    set_driver_factory(None)


def test_unanswerable_required_field_stops(client, llm):
    w = form_app(client, llm)
    fields = [*STANDARD, FormField("x", "What is your shoe size?", "text", True)]
    set_driver_factory(lambda: FakeDriver(fields))
    r = client.post(f"/api/applications/{w.app_id}/form/prepare", headers=w.headers)
    assert r.status_code == 409 and "shoe size" in r.json()["detail"]
    set_driver_factory(None)


def test_unconfirmed_or_changed_form_is_not_marked_sent(client, llm):
    w = form_app(client, llm)
    with w.session() as s:
        app = s.get(Application, w.app_id)
        forms.prepare_form(s, llm, app, FakeDriver(list(STANDARD)))
        forms.approve_form(s, app, NOW)
        changed = FakeDriver([*STANDARD, FormField("new", "Expected salary", "text", True)])
        with pytest.raises(forms.FormStopped, match="changed"):
            forms.submit_form(s, app, now=NOW, driver=changed)
        assert app.status == "manual_apply" and not changed.submitted
    w2 = form_app(client, llm, email="b@example.com")
    with w2.session() as s:
        app = s.get(Application, w2.app_id)
        forms.prepare_form(s, llm, app, FakeDriver(list(STANDARD)))
        forms.approve_form(s, app, NOW)
        silent = FakeDriver(list(STANDARD), success_text="Something went wrong")
        with pytest.raises(forms.FormStopped, match="did not confirm"):
            forms.submit_form(s, app, now=NOW, driver=silent)
        assert app.status == "manual_apply" and app.sent_at is None


def test_form_obeys_send_rules_and_waits_for_the_window(client, llm):
    w = form_app(client, llm)
    driver = FakeDriver(list(STANDARD))
    set_driver_factory(lambda: driver)
    with w.session() as s:
        app = s.get(Application, w.app_id)
        forms.prepare_form(s, llm, app, driver)
        forms.approve_form(s, app, NOW)
        with pytest.raises(rules.RuleViolation) as e:
            forms.submit_form(s, app, now=NOW.replace(hour=22), driver=driver)
        assert e.value.code == "outside_window" and app.status == "form_ready" and not driver.submitted
        campaign_of(s, w).state = "paused"
        with pytest.raises(rules.RuleViolation):
            forms.submit_form(s, app, now=NOW, driver=driver)
        campaign_of(s, w).state = "active"
    with w.session() as s:                                  # the scheduler submits it inside the window
        assert agent_runs.dispatch_run(s, w.user_id, NOW)["sent"] == [str(w.app_id)]
    assert driver.submitted
    set_driver_factory(None)


def test_auto_submit_only_after_clean_reviews_and_never_generic(client, llm):
    w = form_app(client, llm, approval_mode="full_auto")
    with w.session() as s:
        user, campaign = s.get(User, w.user_id), s.get(Campaign, w.campaign_id)
        assert not forms.auto_submit_allowed(s, user, campaign, "greenhouse")
        from app import audit

        for _ in range(get_settings().form_auto_submit_after):
            audit.record(s, user_id=w.user_id, actor="user", tool="submit_form", result={"submitted": True, "hiring_system": "greenhouse"})
        assert forms.auto_submit_allowed(s, user, campaign, "greenhouse")
        assert not forms.auto_submit_allowed(s, user, campaign, "lever")
        assert not forms.auto_submit_allowed(s, user, campaign, "generic")
        campaign.approval_mode = "review"
        assert not forms.auto_submit_allowed(s, user, campaign, "greenhouse")


def test_form_endpoints_are_isolated(client, llm):
    w = form_app(client, llm)
    other = make_world(client, llm, email="b@example.com", mail=w.mail)
    for method, path in [("post", "form/prepare"), ("get", "form"), ("get", "form/screenshot"), ("post", "form/approve")]:
        assert getattr(client, method)(f"/api/applications/{w.app_id}/{path}", headers=other.headers).status_code == 404


# ---- A real browser against local pages ----

CHROME = "/usr/bin/google-chrome"
needs_chrome = pytest.mark.skipif(not os.path.exists(CHROME), reason="no local Chrome")


@pytest.fixture
def site():
    posts = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            name = self.path.strip("/") or "form.html"
            body = (FIX / name).read_bytes() if (FIX / name).exists() else b"<html><body>Not here</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Thank you for applying</h1></body></html>")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", posts
    server.shutdown()


@needs_chrome
def test_real_browser_reads_fills_and_submits(client, llm, site, monkeypatch):
    from app.connectors.browser import PlaywrightDriver

    base, posts = site
    monkeypatch.setenv("BROWSER_EXECUTABLE", CHROME)
    get_settings.cache_clear()
    w = form_app(client, llm, url=f"{base}/form.html")
    with w.session() as s:
        app = s.get(Application, w.app_id)
        s.get(Posting, app.posting_id).url = f"{base}/form.html"
        driver = PlaywrightDriver()
        try:
            forms.prepare_form(s, llm, app, driver)
            labels = [f["label"] for f in app.form_data["fields"]]
            assert labels[:3] == ["First name", "Last name", "Email"] and "Are you legally authorized to work in Austria?" in labels
            assert app.status == "form_ready" and not posts            # filled, not submitted
            forms.approve_form(s, app, NOW)
            forms.submit_form(s, app, now=NOW, driver=driver)
        finally:
            driver.close()
        assert app.status == "sent"
    body = posts[0]
    assert b'name="first_name"\r\n\r\nAlex' in body and b'name="notice"\r\n\r\n1 month' in body
    assert b'filename="cv.txt"' in body and b"Alex Example" in body       # the CV file was uploaded
    assert b'name="auth"\r\n\r\n1' in body and b'name="privacy"\r\n\r\non' in body
    assert b"Prefer not to say" in body                                   # the optional gender question is declined
    get_settings.cache_clear()


@needs_chrome
@pytest.mark.parametrize("page,kind", [("captcha.html", "captcha"), ("login.html", "login")])
def test_real_browser_detects_stop_conditions(site, monkeypatch, page, kind):
    from app.connectors.browser import PlaywrightDriver

    monkeypatch.setenv("BROWSER_EXECUTABLE", CHROME)
    get_settings.cache_clear()
    driver = PlaywrightDriver()
    try:
        driver.open(f"{site[0]}/{page}")
        assert kind in [b.kind for b in driver.blockers()]
    finally:
        driver.close()
    get_settings.cache_clear()


def test_expired_form_draft_is_skipped(client, llm):
    from app.services import applications as app_service

    w = form_app(client, llm)
    with w.session() as s:
        assert app_service.expire_drafts(s, w.user_id, NOW + timedelta(days=30)) == 0    # manual-apply items do not expire
