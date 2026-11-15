"""Forgotten passwords (a one-time reset link by email), changing a password, and the admin's reset link."""

import re

import pytest

from app.config import get_settings
from app.connectors.notify_email import FakeNotifier, set_notifier
from app.security import create_access_token
from tests.conftest import make_user

PW = "correct horse battery"


@pytest.fixture
def mailbox():
    fake = FakeNotifier()
    set_notifier(fake)
    yield fake
    set_notifier(None)


def _link_token(body: str) -> str:
    return re.search(r"/reset\?token=(\S+)", body).group(1)


def test_a_forgotten_password_is_reset_by_a_one_time_link(client, mailbox):
    make_user(client, email="a@example.com")
    assert client.post("/api/auth/forgot", json={"email": "nobody@example.com"}).json() == {"ok": True}
    assert mailbox.sent == []                                           # same answer, nothing sent
    assert client.post("/api/auth/forgot", json={"email": "A@example.com"}).json() == {"ok": True}
    to, subject, body = mailbox.sent[0]
    assert to == "a@example.com" and "reset" in subject.lower() and "Hi Alex" in body
    token = _link_token(body)

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401  # not a sign-in
    assert client.post("/api/auth/reset", json={"token": token, "password": "short"}).status_code == 422
    r = client.post("/api/auth/reset", json={"token": token, "password": "a brand new password"})
    assert r.status_code == 200 and r.json()["access_token"]
    assert client.post("/api/auth/reset", json={"token": token, "password": "another new password"}).status_code == 400  # used
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": PW}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "a brand new password"}).status_code == 200
    assert client.post("/api/auth/reset", json={"token": create_access_token(__import__("uuid").uuid4()),
                                                "password": "a brand new password"}).status_code == 400


def test_reset_emails_are_limited(client, mailbox):
    make_user(client, email="a@example.com")
    for _ in range(5):
        client.post("/api/auth/forgot", json={"email": "a@example.com"})
    assert len(mailbox.sent) == 3


def test_changing_the_password_needs_the_current_one(client):
    headers, _ = make_user(client, email="a@example.com")
    bad = client.post("/api/auth/password", json={"current_password": "wrong one", "new_password": "a brand new password"},
                      headers=headers)
    assert bad.status_code == 400
    ok = client.post("/api/auth/password", json={"current_password": PW, "new_password": "a brand new password"},
                     headers=headers)
    assert ok.status_code == 200
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "a brand new password"}).status_code == 200


def test_the_admin_can_make_a_reset_link(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    get_settings.cache_clear()
    try:
        admin, _ = make_user(client, email="admin@example.com")
        user, user_id = make_user(client, email="b@example.com")
        assert client.post(f"/api/operator/users/{user_id}/reset-link", headers=user).status_code == 404
        link = client.post(f"/api/operator/users/{user_id}/reset-link", headers=admin).json()["link"]
        token = link.split("token=")[1]
        assert client.post("/api/auth/reset", json={"token": token, "password": "a brand new password"}).status_code == 200
    finally:
        monkeypatch.delenv("ADMIN_EMAILS")
        get_settings.cache_clear()
