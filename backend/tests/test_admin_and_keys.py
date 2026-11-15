"""Model keys (the operator's key for everyone, or each user's own), the admin panel, and filling web
forms automatically with two tries and a log of what went wrong."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.browser import Blocker, FakeDriver, set_driver_factory
from app.llm import access, set_llm_override
from app.models import Application, Campaign, User
from app.services import forms
from tests.helpers import make_world
from tests.test_phase3 import STANDARD, form_app

KEY = "AIzaSyTestKeyForTheUnitTests1234"


def _env(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


@pytest.fixture
def admin_env(monkeypatch):
    _env(monkeypatch, ADMIN_EMAILS="a@example.com")
    yield
    monkeypatch.delenv("ADMIN_EMAILS")
    get_settings.cache_clear()


def test_each_policy_picks_the_right_key(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        user = s.get(User, w.user_id)
        assert access.source_for(user) == "platform"                       # default "either", no own key
        access.set_own_key(user, KEY)
        assert access.key_hint(user) == "…1234" and user.llm_api_key_enc != KEY   # stored encrypted
        assert access.source_for(user) == "own" and access.cap_for(user) == 0.0   # own key: the user's limit
        access.save_platform_settings(key_policy="platform", default_cap_usd=2.5)
        assert access.source_for(user) == "platform" and access.cap_for(user) == 2.5
        user.platform_cap_usd = 0.5
        assert access.cap_for(user) == 0.5                                  # the admin's per-user limit
        access.save_platform_settings(key_policy="own")
        access.set_own_key(user, "")
        assert access.source_for(user) == "none"
        set_llm_override(None)
        try:
            with pytest.raises(access.NoModelKey, match="add your own key"):
                access.client_for(s, w.user_id)
        finally:
            set_llm_override(llm)
        access.save_platform_settings(key_policy="either")
        user.platform_key_allowed = False
        assert access.source_for(user) == "none"
    with pytest.raises(ValueError):
        access.save_platform_settings(key_policy="everyone")


def test_users_add_and_remove_their_own_key(client, llm):
    w = make_world(client, llm)
    r = client.get("/api/account/model-key", headers=w.headers).json()
    assert r["source"] == "platform" and not r["has_own_key"]
    assert client.put("/api/account/model-key", json={"key": "short"}, headers=w.headers).status_code == 400
    r = client.put("/api/account/model-key", json={"key": KEY, "own_key_cap_usd": 3}, headers=w.headers).json()
    assert r["source"] == "own" and r["hint"] == "…1234" and r["cap_usd"] == 3.0
    assert KEY not in str(r)
    r = client.delete("/api/account/model-key", headers=w.headers).json()
    assert r["source"] == "platform" and not r["has_own_key"]


def test_admin_panel_is_only_for_admins(client, llm, admin_env):
    admin = make_world(client, llm)
    other = make_world(client, llm, email="b@example.com")
    assert client.get("/api/auth/me", headers=admin.headers).json()["is_admin"] is True
    assert client.get("/api/auth/me", headers=other.headers).json()["is_admin"] is False
    for path in ("/api/operator/users", "/api/operator/settings", "/api/operator/form-attempts"):
        assert client.get(path, headers=other.headers).status_code == 404
    r = client.put("/api/operator/settings", json={"key_policy": "own", "default_cap_usd": 4}, headers=admin.headers)
    assert r.status_code == 200 and r.json()["key_policy"] == "own"
    assert client.put("/api/operator/settings", json={"key_policy": "x"}, headers=admin.headers).status_code == 422
    r = client.patch(f"/api/operator/users/{other.user_id}", json={"platform_cap_usd": 0.25, "daily_send_limit": 5},
                     headers=admin.headers).json()
    assert r["platform_cap_usd"] == 0.25 and r["daily_send_limit"] == 5
    users = {u["email"]: u for u in client.get("/api/operator/users", headers=admin.headers).json()}
    assert users["b@example.com"]["key_source"] == "none"                  # policy "own" and no key yet
    assert client.patch(f"/api/operator/users/{admin.user_id}", json={"is_operator": False},
                        headers=admin.headers).status_code == 422              # cannot lock yourself out


def test_forms_are_filled_automatically_with_two_tries_and_a_log(client, llm, admin_env):
    w = form_app(client, llm)
    with w.session() as s:
        campaign = s.execute(select(Campaign).where(Campaign.user_id == w.user_id)).scalar_one()
        campaign.config = {**campaign.config, "auto_fill_forms": True}
    set_driver_factory(lambda: FakeDriver(list(STANDARD), blockers=[Blocker("captcha", "the page shows a CAPTCHA")]))
    try:
        for expected in (1, 2):
            with w.session() as s:
                campaign = s.execute(select(Campaign).where(Campaign.user_id == w.user_id)).scalar_one()
                out = forms.auto_fill(s, llm, campaign)
                assert out["failed"] == [str(w.app_id)] and forms.form_attempts(s, w.app_id) == expected
        with w.session() as s:                                               # two tries used: left for the user
            campaign = s.execute(select(Campaign).where(Campaign.user_id == w.user_id)).scalar_one()
            assert forms.auto_fill(s, llm, campaign) == {"filled": [], "failed": []}
            app = s.get(Application, w.app_id)
            assert app.status == "manual_apply" and "CAPTCHA" in app.skip_reason
        log = client.get("/api/operator/form-attempts", headers=w.headers).json()
        assert log["failed"] == 2 and log["by_kind"] == {"CAPTCHA": 2}
        assert [a["attempt"] for a in log["attempts"]] == [2, 1] and log["attempts"][0]["final"] is True
    finally:
        set_driver_factory(None)


def test_a_simple_form_is_filled_and_waits_for_approval(client, llm):
    w = form_app(client, llm)
    set_driver_factory(lambda: FakeDriver(list(STANDARD)))
    try:
        with w.session() as s:
            campaign = s.execute(select(Campaign).where(Campaign.user_id == w.user_id)).scalar_one()
            out = forms.auto_fill(s, llm, campaign)
            assert out == {"filled": [str(w.app_id)], "failed": []}
            assert s.get(Application, w.app_id).status == "form_ready"      # submitted only after approval
    finally:
        set_driver_factory(None)


def test_the_interest_question_gets_the_cover_note(client, llm):
    from app.connectors.browser import FormField
    from app.services.campaigns import CampaignConfig

    field = FormField("f1", "Why are you interested in this role?", "textarea", False)
    user = User(email="a@example.com", name="Alex Example")
    value = forms.standard_answer(field, user=user, profile={}, facts={}, config=CampaignConfig(), country=None,
                                  cover_text="Dear team")
    assert value == "Dear team"
