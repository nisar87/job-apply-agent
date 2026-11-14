"""Settings for a private deployment: invite-only sign-up, hidden from search engines, scheduled work
called over HTTP, and manual runs that finish after the response."""

import time

from sqlalchemy import select

from app.config import get_settings
from app.models import Run
from tests.helpers import make_world


def _env(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def test_sign_up_is_invite_only_when_an_allowlist_is_set(client, monkeypatch):
    _env(monkeypatch, SIGNUP_ALLOWLIST="Me@Example.com, other@example.com")
    body = {"password": "correct horse battery", "name": "X"}
    r = client.post("/api/auth/signup", json={"email": "stranger@example.com", **body})
    assert r.status_code == 403 and "invitation" in r.json()["detail"]
    assert client.post("/api/auth/signup", json={"email": "me@example.com", **body}).status_code == 201
    _env(monkeypatch, SIGNUP_ALLOWLIST="")
    assert client.post("/api/auth/signup", json={"email": "anyone@example.com", **body}).status_code == 201


def test_hidden_deployment_tells_search_engines_to_stay_out(client, monkeypatch):
    assert "X-Robots-Tag" not in client.get("/api/health").headers
    _env(monkeypatch, HIDE_FROM_SEARCH="true")
    assert client.get("/api/health").headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"


def test_scheduled_jobs_need_the_shared_token(client, monkeypatch):
    assert client.post("/api/system/run/tick").status_code == 403          # no token configured: never open
    _env(monkeypatch, SCHEDULER_TOKEN="s3cret-token")
    assert client.post("/api/system/run/tick", headers={"X-Scheduler-Token": "wrong"}).status_code == 403
    assert client.post("/api/system/run/nope", headers={"X-Scheduler-Token": "s3cret-token"}).status_code == 404
    r = client.post("/api/system/run/tick", headers={"X-Scheduler-Token": "s3cret-token"})
    assert r.status_code == 200 and r.json()["job"] == "tick" and "users" in r.json()["result"]


def test_manual_runs_can_finish_after_the_response(client, llm, monkeypatch):
    w = make_world(client, llm)
    _env(monkeypatch, MANUAL_RUNS_IN_BACKGROUND="true")
    r = client.post(f"/api/campaigns/{w.campaign_id}/run?kind=discovery", headers=w.headers)
    assert r.status_code == 200 and r.json() == {"started": True, "kind": "discovery"}
    for _ in range(50):
        with w.session() as s:
            runs = s.execute(select(Run).where(Run.type == "discovery")).scalars().all()
        if runs and runs[0].state != "running":
            break
        time.sleep(0.2)
    assert runs and runs[0].state == "done"
