"""Jobs applied to on the employer's own site, and Gmail's weekly reconnect."""

from datetime import timedelta

from sqlalchemy import select

from app.agent import runs as agent_runs
from app.connectors.mail import MailAuthError
from app.models import Application, Campaign, Notification
from app.services import applications as app_service
from app.services import matching
from app.services import postings as posting_service
from app.services.campaigns import get_config
from tests.helpers import NOW, always_ok, campaign_of, make_world, raw
from tests.test_phase1_rules import queued_app


def form_app(s, w, llm, title="Platform Engineer", company="Formy GmbH"):
    posting = posting_service.ingest(s, llm, raw(title=title, company=company, email="", requirements="python"),
                                     mail_check=always_ok)[0]
    campaign = campaign_of(s, w)
    match = matching.evaluate(s, llm, campaign, get_config(campaign), posting, {}, [])
    assert match.decision == "manual"
    return app_service.create_draft(s, llm, campaign, match)


def test_an_on_site_job_costs_no_email_until_the_form_is_filled(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        writes = len([c for c in llm.calls if c["task"] == "write_application"])
        app = form_app(s, w, llm)
        assert app.status == "manual_apply" and app.route == "form" and app.body == ""
        assert len([c for c in llm.calls if c["task"] == "write_application"]) == writes   # nothing written
        assert app.expires_at >= NOW + timedelta(days=app_service.MANUAL_VALID_DAYS - 1)
        note = app_service.write_cover_note(s, llm, app)
        assert note and app.body == note
        assert app_service.write_cover_note(s, llm, app) == note    # written once


def test_i_applied_and_skip(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        a = form_app(s, w, llm, title="Platform Engineer", company="Alpha GmbH")
        b = form_app(s, w, llm, title="Platform Engineer", company="Beta GmbH")
        a_id, b_id = a.id, b.id
    r = client.post(f"/api/applications/{a_id}/applied", headers=w.headers)
    assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["sent_at"]
    r = client.post(f"/api/applications/{b_id}/skip", headers=w.headers)
    assert r.status_code == 200 and r.json()["status"] == "skipped"
    assert client.post(f"/api/applications/{a_id}/applied", headers=w.headers).status_code == 409   # only once
    other = make_world(client, llm, email="b@example.com")
    assert client.post(f"/api/applications/{b_id}/skip", headers=other.headers).status_code == 404  # not theirs


def test_old_on_site_jobs_leave_the_list(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = form_app(s, w, llm)
        app.expires_at = NOW - timedelta(minutes=1)
        assert app_service.expire_drafts(s, w.user_id, NOW) == 1
        assert app.status == "skipped" and "employer's site" in app.skip_reason


def test_reconnecting_gmail_resumes_the_searches_it_paused(client, llm):
    w = make_world(client, llm)

    def broken(account, mail):
        raise MailAuthError("token expired")

    with w.session() as s:
        app = queued_app(s, w, llm)
        w.mail.send = broken
        try:
            app_service.send(s, app, NOW)
        except Exception:  # noqa: BLE001 - the rule violation is expected
            pass
    with w.session() as s:
        assert campaign_of(s, w).state == "paused"
        other = Campaign(user_id=w.user_id, name="Paused by me", state="paused", config=campaign_of(s, w).config)
        s.add(other)
    r = client.post("/api/mail/fake/connect", headers=w.headers, json={"address": "a@example.com"})
    assert r.status_code == 200
    with w.session() as s:
        states = {c.name: c.state for c in s.execute(select(Campaign)).scalars()}
    assert states == {"Austria remote": "active", "Paused by me": "paused"}   # only the one Gmail paused


def test_the_notice_lists_on_site_jobs_too(client, llm):
    w = make_world(client, llm)
    from app.connectors.sources import FakeSource, set_sources

    set_sources({"fake": FakeSource([raw(title="Backend Engineer", company="Mail GmbH", email="jobs@mail.at"),
                                     raw(title="Platform Engineer", company="Formy GmbH", email="", requirements="python")])})
    with w.session() as s:
        agent_runs.discovery_run(s, llm, campaign_of(s, w), mail_check=always_ok)
        agent_runs.prepare_run(s, llm, campaign_of(s, w))
        note = s.execute(select(Notification).where(Notification.kind == "drafts_ready")).scalar_one()
        statuses = {a.status for a in s.execute(select(Application)).scalars()}
    assert {"awaiting_approval", "manual_apply"} <= statuses
    assert "plus 1 to apply on site" in note.title and "Formy" not in note.title
    assert "Apply on the employer's site" in note.body


def test_drafts_say_why_they_matched(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app_id = queued_app(s, w, llm, approve=False).id
    out = client.get("/api/applications?status=awaiting_approval", headers=w.headers).json()[0]
    assert out["id"] == str(app_id) and out["match_strengths"]
