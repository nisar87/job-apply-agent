"""On the test site, only users the admin switched to real sending reach employers."""

import uuid

import pytest

from app.config import get_settings
from app.services import applications as app_service
from app.services import forms, live, rules
from tests.helpers import make_world


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("SANDBOX_RECIPIENT", "sandbox@example.com")
    monkeypatch.setenv("FRONTEND_URL", "https://jobs.test.example")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_only_switched_users_reach_real_employers(staging):
    me, other = uuid.uuid4(), uuid.uuid4()
    live.set_real(me, True)
    assert app_service._recipient_for_environment("hr@employer.de", me) == "hr@employer.de"
    assert app_service._recipient_for_environment("hr@employer.de", other) == "sandbox@example.com"
    assert app_service._recipient_for_environment("hr@employer.de") == "sandbox@example.com"
    forms._check_form_sandbox("https://jobs.employer.de/apply", me)          # allowed for me
    with pytest.raises(rules.RuleViolation):
        forms._check_form_sandbox("https://jobs.employer.de/apply", other)   # still refused for others
    live.set_real(me, False)
    assert app_service._recipient_for_environment("hr@employer.de", me) == "sandbox@example.com"


def test_the_admin_switches_real_sending_per_user(client, llm, staging):
    w = make_world(client, llm)
    admin_headers = w.headers
    with w.session() as s:
        from app.models import User

        me = s.get(User, w.user_id)
        me.is_operator = True
        s.commit()
    users = client.get("/api/operator/users", headers=admin_headers).json()
    assert users[0]["real_sending"] is False and users[0]["production"] is False
    row = client.patch(f"/api/operator/users/{w.user_id}", headers=admin_headers, json={"real_sending": True}).json()
    assert row["real_sending"] is True and live.sends_for_real(w.user_id)
