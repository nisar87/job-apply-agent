"""The report of what the agent applied to, emailed to the user's main address."""

from datetime import timedelta

from app.models import User
from app.services import applications as app_service
from app.services import report
from tests.helpers import NOW, make_world
from tests.test_phase1_rules import queued_app


def test_the_report_names_each_job_its_link_the_address_and_where_it_came_from(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm, title="Backend Engineer", company="Acme GmbH", email="jobs@acme.at")
        app_service.send(s, app, NOW)
        posting = s.get(app_service.Posting, app.posting_id)
        waiting = queued_app(s, w, llm, approve=False, title="Data Engineer", company="Beta GmbH", email="hr@beta.at")
        user = s.get(User, w.user_id)
        d = report.build(s, user, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
        subject, body = report.render(user, d, "daily")
    assert subject == "Report: 1 application sent today, 1 waiting for you"
    assert "1. Backend Engineer at Acme GmbH" in body and f"Job ad: {posting.url}" in body
    assert "Sent to: jobs@acme.at" in body and "Address: written in the job ad" in body
    assert "Data Engineer at Beta GmbH" in body and "Would go to: hr@beta.at" in body
    assert "/account" in body and waiting.status == "awaiting_approval"


def test_an_address_found_on_the_employers_site_is_named_with_its_page():
    class P:
        url, description, fields = "https://www.stepstone.de/job/1", "Apply online", {"contact_source": "https://www.formy.de/karriere"}

    class A:
        route, recipient = "email", "jobs@formy.de"

    assert report.where_from(P, A) == "published on the employer's website: https://www.formy.de/karriere"
    A.route = "form"
    assert report.where_from(P, A) == "the employer's web form (https://www.stepstone.de/job/1)"


def test_reports_go_to_the_main_address_once_a_day_after_the_chosen_hour(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        user = s.get(User, w.user_id)
        user.timezone, user.report_hour = "UTC", 20
        evening = NOW.replace(hour=20, minute=5)
        assert not report.due(user, NOW.replace(hour=19))
        assert report.due(user, evening)
        assert report.send(s, user, evening)
        assert not report.due(user, evening + timedelta(hours=2))          # once a day
        assert report.due(user, evening + timedelta(days=1))
        user.report_frequency = "off"
        assert not report.due(user, evening + timedelta(days=1))
        user.report_frequency = "weekly"
        monday = evening + timedelta(days=(7 - evening.weekday()) % 7 or 7)
        assert report.due(user, monday) and not report.due(user, monday + timedelta(days=1))
    sent = [mail for _, mail, _ in w.mail.sent if "Report:" in mail.subject]
    assert len(sent) == 1 and sent[0].to == "me@example.com"      # the main address, not the job Gmail


def test_report_settings_preview_and_send_now_through_the_api(client, llm):
    w = make_world(client, llm)
    me = client.patch("/api/auth/me", headers=w.headers, json={"report_frequency": "weekly", "report_hour": 18}).json()
    assert (me["report_frequency"], me["report_hour"]) == ("weekly", 18)
    assert client.patch("/api/auth/me", headers=w.headers, json={"report_frequency": "hourly"}).status_code == 422
    preview = client.get("/api/report/preview", headers=w.headers).json()
    assert preview["to"] == "me@example.com" and "Applications sent (0)" in preview["body"]
    r = client.post("/api/report/send-now", headers=w.headers)
    assert r.status_code == 200 and r.json() == {"sent_to": "me@example.com"}
