"""The real connectors, exercised against recorded-shape HTTP responses."""

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.config import get_settings
from app.connectors import mail as mail_mod
from app.connectors.fetcher import Fetcher, FetchRefused, html_to_text
from app.connectors.mail import Attachment, GmailProvider, MailAuthError, OutgoingMail, build_mime
from app.connectors.sources import (
    AdzunaSource,
    ArbeitnowSource,
    FakeSource,
    GreenhouseSource,
    LeverSource,
    PersonioSource,
    SearchQuery,
    set_sources,
)
from app.models import MailAccount
from app.security import encrypt_secret
from app.tasks import scheduler
from tests.helpers import NOW, always_ok, make_world
from tests.test_phase1_agent import POSTINGS


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def account() -> MailAccount:
    import uuid

    return MailAccount(id=uuid.uuid4(), user_id=uuid.uuid4(), address="me@example.com", provider="gmail",
                       encrypted_refresh_token=encrypt_secret("refresh-1"), last_history_id="100")


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_mime_has_attachment_and_reply_headers():
    msg = build_mime("me@example.com", OutgoingMail(to="hr@acme.at", subject="Bewerbung", body="Guten Tag", from_name="Alex Example",
                                                    attachments=[Attachment("cv.pdf", b"%PDF-1.4")], in_reply_to="<abc@mail>"))
    assert msg["To"] == "hr@acme.at" and msg["In-Reply-To"] == "<abc@mail>" and "Alex Example" in msg["From"]
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ["cv.pdf"]


def test_gmail_send_refreshes_token_and_posts_raw_message():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "oauth2.googleapis.com":
            assert b"refresh_token=refresh-1" in req.content
            return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer at-1"
        body = json.loads(req.content)
        raw = base64.urlsafe_b64decode(body["raw"]).decode()
        assert "To: hr@acme.at" in raw and body["threadId"] == "t9"
        return httpx.Response(200, json={"id": "m1", "threadId": "t9"})

    gmail = GmailProvider(client_for(handler))
    acct = account()
    sent = gmail.send(acct, OutgoingMail(to="hr@acme.at", subject="Re: Role", body="Hello", thread_id="t9"))
    assert (sent.message_id, sent.thread_id) == ("m1", "t9") and sent.rfc_message_id
    gmail.send(acct, OutgoingMail(to="hr@acme.at", subject="x", body="y", thread_id="t9"))
    assert sum(r.url.host == "oauth2.googleapis.com" for r in seen) == 1      # the access token is reused


def test_gmail_revoked_token_raises_auth_error():
    gmail = GmailProvider(client_for(lambda req: httpx.Response(400, json={"error": "invalid_grant"})))
    with pytest.raises(MailAuthError):
        gmail.send(account(), OutgoingMail(to="a@b.at", subject="s", body="b"))


def test_gmail_thread_and_history_parsing():
    message = {
        "id": "m2", "threadId": "t9", "internalDate": "1794300000000",
        "payload": {"mimeType": "multipart/alternative", "headers": [
            {"name": "From", "value": "Maria Huber <hr@acme.at>"}, {"name": "To", "value": "me@example.com"},
            {"name": "Subject", "value": "Re: Bewerbung"}, {"name": "Message-ID", "value": "<r1@acme.at>"}],
            "parts": [{"mimeType": "text/plain", "body": {"data": b64("Vielen Dank für Ihre Bewerbung.")}},
                      {"mimeType": "text/html", "body": {"data": b64("<p>Vielen Dank</p>")}}]},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if req.url.path.endswith("/threads/t9"):
            return httpx.Response(200, json={"messages": [message]})
        if req.url.path.endswith("/history"):
            assert req.url.params["startHistoryId"] == "100"
            return httpx.Response(200, json={"historyId": "140", "history": [
                {"messagesAdded": [{"message": {"id": "m2", "threadId": "t9"}}]}]})
        if req.url.path.endswith("/watch"):
            return httpx.Response(200, json={"historyId": "150", "expiration": "1794900000000"})
        return httpx.Response(404)

    gmail = GmailProvider(client_for(handler))
    msgs = gmail.get_thread(account(), "t9")
    assert msgs[0].sender == "hr@acme.at" and msgs[0].body.startswith("Vielen Dank") and msgs[0].rfc_message_id == "<r1@acme.at>"
    refs, latest = gmail.list_new(account())
    assert [r.message_id for r in refs] == ["m2"] and latest == "140"
    assert gmail.watch(account()) == (None, None)                 # no topic configured: watching is skipped


def test_gmail_scopes_are_the_two_narrow_ones():
    assert mail_mod.GMAIL_SCOPES == ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]


def test_adzuna_source(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    get_settings.cache_clear()

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/api/jobs/at/search/1" and req.url.params["max_days_old"] == "7"
        return httpx.Response(200, json={"results": [{
            "id": 42, "title": "<strong>Python</strong> Developer", "company": {"display_name": "Acme GmbH"},
            "location": {"display_name": "Wien"}, "description": "Great <b>job</b>…", "created": "2026-11-08T10:00:00Z",
            "redirect_url": "https://www.adzuna.at/land/ad/42", "salary_min": 50000, "contract_time": "full_time"}]})

    out = AdzunaSource(client_for(handler)).search(SearchQuery(keywords=["python"], country="at", max_days_old=7))
    assert out[0].title == "Python Developer" and out[0].is_snippet and out[0].posted_date.isoformat() == "2026-11-08"
    assert out[0].salary_min == 50000 and out[0].country == "at"
    get_settings.cache_clear()


def test_adzuna_without_key_returns_nothing():
    assert AdzunaSource(client_for(lambda r: httpx.Response(500))).search(SearchQuery(keywords=["x"])) == []


def test_arbeitnow_greenhouse_lever_personio():
    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host == "www.arbeitnow.com":
            return httpx.Response(200, json={"data": [
                {"slug": "a1", "company_name": "Bergwerk", "title": "Python Engineer", "description": "<p>Remote role</p>",
                 "remote": True, "url": "https://arbeitnow.com/a1", "tags": ["python"], "job_types": ["full time"],
                 "location": "Vienna", "created_at": 1794300000},
                {"slug": "a2", "company_name": "X", "title": "Chef", "description": "kitchen", "remote": False, "url": "u",
                 "tags": [], "job_types": [], "location": "Graz", "created_at": 1794300000}]})
        if host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": [{"id": 7, "title": "Python Engineer", "absolute_url": "https://gh/7",
                                                       "location": {"name": "Vienna"}, "updated_at": "2026-11-09T08:00:00-05:00",
                                                       "content": "&lt;p&gt;Write python&lt;/p&gt;"}]})
        if host == "api.lever.co":
            return httpx.Response(200, json=[{"id": "L1", "text": "Python Engineer", "hostedUrl": "https://lever/L1",
                                              "categories": {"location": "Remote", "commitment": "Full-time"},
                                              "descriptionPlain": "python work", "createdAt": 1794300000000, "workplaceType": "remote",
                                              "lists": [{"content": "<li>3 years python</li>"}]}])
        if host == "acme.jobs.personio.de":
            return httpx.Response(200, text="""<workzag-jobs><position><id>55</id><office>Wien</office><name>Python Engineer</name>
                <schedule>full-time</schedule><createdAt>2026-11-07T09:00:00+00:00</createdAt><jobDescriptions><jobDescription>
                <name>Tasks</name><value><![CDATA[<p>Build python services</p>]]></value></jobDescription></jobDescriptions>
                </position></workzag-jobs>""")
        return httpx.Response(404)

    http = client_for(handler)
    q = SearchQuery(keywords=["python"], company_slugs=["acme"])
    arbeit = ArbeitnowSource(http).search(q)
    assert len(arbeit) == 1 and arbeit[0].work_mode == "remote" and arbeit[0].description == "Remote role"
    gh = GreenhouseSource(http).search(q)
    assert gh[0].description == "Write python" and gh[0].source_id == "acme:7"
    lever = LeverSource(http).search(q)
    assert lever[0].work_mode == "remote" and "3 years python" in lever[0].description
    personio = PersonioSource(http).search(q)
    assert personio[0].url == "https://acme.jobs.personio.de/job/55" and "Build python services" in personio[0].description


def test_html_to_text_drops_scripts():
    assert html_to_text("<p>Hello</p><script>steal()</script><style>x{}</style><li>World</li>") == "Hello\nWorld"


def test_fetcher_obeys_robots_and_refuses_blocks():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        if req.url.path == "/blocked":
            return httpx.Response(403)
        return httpx.Response(200, text="<h1>Backend Engineer</h1><p>Apply to jobs@acme.at</p>")

    f = Fetcher(client_for(handler), use_cache=False, min_gap=0)
    assert "jobs@acme.at" in f.fetch_text("https://acme.at/jobs/1")
    with pytest.raises(FetchRefused, match="robots"):
        f.fetch_text("https://acme.at/private/1")
    with pytest.raises(FetchRefused, match="403"):
        f.fetch_text("https://acme.at/blocked")
    with pytest.raises(FetchRefused):
        f.fetch_text("file:///etc/passwd")


def test_scheduler_tick_runs_discovery_prepare_and_dispatch(client, llm):
    w = make_world(client, llm, approval_mode="full_auto")
    set_sources({"fake": FakeSource(list(POSTINGS))})
    first = scheduler.tick_user(w.user_id, NOW, mail_check=always_ok)
    assert first["discovery"] == 1 and first["prepare"] == 1 and not first["errors"]
    again = scheduler.tick_user(w.user_id, NOW.replace(minute=5), mail_check=always_ok)
    assert again["discovery"] == 0 and again["prepare"] == 0          # not due again yet
    sent = first["sent"] + again["sent"]
    for i in range(2, 12):
        sent += scheduler.tick_user(w.user_id, NOW.replace(minute=5 * i), mail_check=always_ok)["sent"]
    applications = [m for _, m, _ in w.mail.sent if not m.subject.startswith("[Job Apply Agent]")]
    assert sent == 2 and len(applications) == 2      # plus a notice to the user about jobs to apply to on site
    night = scheduler.tick_user(w.user_id, datetime(2026, 11, 10, 22, 0, tzinfo=UTC), mail_check=always_ok)
    assert night["prepare"] == 0 and night["sent"] == 0               # outside the window nothing is drafted or sent


def test_allocation_runs_once_per_day(client, llm):
    assert scheduler.allocate_places(NOW) == 0
    assert scheduler.allocate_places(NOW) == 0
