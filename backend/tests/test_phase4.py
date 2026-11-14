"""Launch preparation: notices by email, export, deletion, employer opt-out, health and sign-in limits."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import storage
from app.connectors.notify_email import FakeNotifier, set_notifier
from app.db import system_session
from app.models import Application, AuditLog, CVVersion, GlobalBlock, MailAccount, Notification, User
from app.services import account as account_service
from app.services.notify import notify
from tests.conftest import make_user
from tests.helpers import make_world
from tests.test_phase2 import reply, sent_world


def test_urgent_notice_is_emailed_at_once_and_others_are_batched_hourly(client, llm):
    w = sent_world(client, llm)
    fake = FakeNotifier()
    set_notifier(fake)
    now = datetime.now(UTC)
    with w.session() as s:
        notify(s, w.user_id, "escalation", "Acme replied", "A question", "/inbox")
        notify(s, w.user_id, "bounce", "Could not deliver", "")
        assert account_service.deliver_notifications(s, w.user_id, now) == 2
        assert fake.sent[0][0] == "me@example.com" and "2 updates" in fake.sent[0][1] and "/inbox" in fake.sent[0][2]
        notify(s, w.user_id, "escalation", "Beta replied", "")
        assert account_service.deliver_notifications(s, w.user_id, now + timedelta(minutes=10)) == 0      # within the hour
        notify(s, w.user_id, "escalation_urgent", "Urgent: interview at Gamma", "")
        assert account_service.deliver_notifications(s, w.user_id, now + timedelta(minutes=11)) == 1      # urgent goes now
        assert fake.sent[-1][1] == "Urgent: interview at Gamma"
        assert account_service.deliver_notifications(s, w.user_id, now + timedelta(minutes=71)) == 1      # the batch is due
        assert account_service.deliver_notifications(s, w.user_id, now + timedelta(minutes=72)) == 0
    set_notifier(None)


def test_without_smtp_notices_go_from_the_users_own_gmail(client, llm):
    w = sent_world(client, llm)
    before = len(w.mail.sent)
    with w.session() as s:
        notify(s, w.user_id, "escalation_urgent", "Urgent", "Interview at Acme")
        assert account_service.deliver_notifications(s, w.user_id) == 1
        assert s.execute(select(Notification).where(Notification.kind == "escalation_urgent")).scalar_one().emailed is True
    _, mail, _ = w.mail.sent[-1]
    assert len(w.mail.sent) == before + 1 and mail.to == "me@example.com"          # the escalation address, never an employer
    assert mail.subject == "[Job Apply Agent] Urgent" and "Interview at Acme" in mail.body and mail.thread_id is None


def test_without_smtp_or_a_mailbox_notices_stay_in_the_app(client, user):
    headers, user_id = user
    import uuid

    from app.db import user_session

    with user_session(uuid.UUID(user_id)) as s:
        notify(s, uuid.UUID(user_id), "escalation_urgent", "Urgent", "")
        assert account_service.deliver_notifications(s, uuid.UUID(user_id)) == 0
        assert s.execute(select(Notification).where(Notification.kind == "escalation_urgent")).scalar_one().emailed is False


def test_export_contains_everything_but_no_secrets(client, llm):
    w = sent_world(client, llm)
    reply(w, "We received your application.")
    data = client.get("/api/account/export", headers=w.headers).json()
    assert data["account"]["email"] == "a@example.com" and len(data["applications"]) == 1 and len(data["messages"]) == 2
    assert data["cv_versions"][0]["profile"]["skills"] and data["fact_sheet"]["notice_period"] == "1 month"
    text = str(data)
    assert "encrypted_refresh_token" not in text and "password_hash" not in text and "fake-token" not in text


def test_delete_account_removes_rows_and_files(client, llm):
    w = sent_world(client, llm)
    other = make_world(client, llm, email="b@example.com", mail=w.mail)
    with w.session() as s:
        key = s.execute(select(CVVersion)).scalars().first().file_key
    assert storage.load(key)
    assert client.post("/api/account/delete", headers=w.headers, json={"password": "wrong"}).status_code == 403
    assert client.post("/api/account/delete", headers=w.headers, json={"password": "correct horse battery"}).status_code == 204
    with system_session() as s:
        assert s.get(User, w.user_id) is None
        for model in (Application, MailAccount, CVVersion, AuditLog, Notification):
            assert s.execute(select(model).where(model.user_id == w.user_id)).first() is None, model.__name__
        assert s.get(User, other.user_id) is not None                      # the other user is untouched
        assert s.execute(select(CVVersion).where(CVVersion.user_id == other.user_id)).first() is not None
    try:
        storage.load(key)
        raise AssertionError("the CV file should be gone")
    except FileNotFoundError:
        pass
    assert client.get("/api/auth/me", headers=w.headers).status_code == 401
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "correct horse battery"}).status_code == 401


def test_employer_block_request_needs_operator_approval(client, llm):
    w = make_world(client, llm)
    r = client.post("/api/public/block-request", json={"contact_email": "HR@Acme.at", "company": "Acme", "message": "Please stop"})
    assert r.status_code == 202
    with system_session() as s:
        assert s.execute(select(GlobalBlock)).first() is None             # nothing is blocked until reviewed
    assert client.get("/api/operator/block-requests", headers=w.headers).status_code == 404
    with system_session() as s:
        s.get(User, w.user_id).is_operator = True
    reqs = client.get("/api/operator/block-requests", headers=w.headers).json()
    assert reqs[0]["contact_email"] == "hr@acme.at"
    assert client.post(f"/api/operator/block-requests/{reqs[0]['id']}?approve=true", headers=w.headers).json()["state"] == "approved"
    with system_session() as s:
        assert {(b.type, b.value) for b in s.execute(select(GlobalBlock)).scalars()} == {("address", "hr@acme.at"), ("domain", "acme.at")}
    assert client.post(f"/api/operator/block-requests/{reqs[0]['id']}?approve=true", headers=w.headers).status_code == 404
    for _ in range(4):
        client.post("/api/public/block-request", json={"contact_email": "x@y.at"})
    assert client.post("/api/public/block-request", json={"contact_email": "x@y.at"}).status_code == 429


def test_operator_health_reports_problems(client, llm):
    w = sent_world(client, llm)
    with system_session() as s:
        s.get(User, w.user_id).is_operator = True
    assert client.get("/api/operator/health", headers=w.headers).json()["ok"] is True
    with system_session() as s:
        s.execute(select(MailAccount)).scalar_one().status = "error"
        app = s.execute(select(Application)).scalar_one()
        app.status = "sending"
        app.updated_at = datetime.now(UTC) - timedelta(hours=1)
    health = client.get("/api/operator/health", headers=w.headers).json()
    kinds = {a["kind"] for a in health["alerts"]}
    assert not health["ok"] and {"mail_disconnected", "stuck_sends"} <= kinds


def test_login_is_rate_limited_and_headers_are_set(client):
    make_user(client)
    for _ in range(10):
        assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "nope nope nope"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "a@example.com", "password": "correct horse battery"})
    assert r.status_code == 429
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_export_requires_login_and_is_per_user(client, llm):
    w = sent_world(client, llm)
    other = make_world(client, llm, email="b@example.com", mail=w.mail)
    assert client.get("/api/account/export").status_code == 401
    assert client.get("/api/account/export", headers=other.headers).json()["applications"] == []
    assert uuid.UUID(str(w.user_id))
