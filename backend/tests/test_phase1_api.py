"""API behaviour, including that one user can never reach another user's data."""


from app.agent import runs as agent_runs
from app.models import Application, Match
from tests.helpers import NOW, campaign_of, make_world
from tests.test_phase1_agent import discovered


def prepared(client, llm, **kw):
    w = make_world(client, llm, **kw)
    discovered(w, llm)
    with w.session() as s:
        agent_runs.prepare_run(s, llm, campaign_of(s, w), NOW)
    return w


def test_tenant_isolation_across_every_endpoint(client, llm):
    a = prepared(client, llm)
    b = make_world(client, llm, email="b@example.com", mail=a.mail)
    app_id = client.get("/api/applications?status=awaiting_approval", headers=a.headers).json()[0]["id"]
    cv = client.get("/api/cvs", headers=a.headers).json()[0]
    match_id = client.get("/api/matches?decision=skip", headers=a.headers).json()[0]["id"]
    account_id = client.get("/api/mail/account", headers=a.headers).json()[0]["id"]
    block_id = client.post("/api/blocks", headers=a.headers, json={"type": "company", "value": "Initech"}).json()["id"]
    pref_id = client.post("/api/preferences", headers=a.headers, json={"text": "no agencies"}).json()["id"]
    h = b.headers
    # Lists show only B's own rows.
    for path in ["/api/applications", "/api/matches?decision=skip,apply,manual", "/api/notifications", "/api/blocks", "/api/preferences"]:
        assert client.get(path, headers=h).json() == [], path
    assert len(client.get("/api/cvs", headers=h).json()) == 1 and len(client.get("/api/campaigns", headers=h).json()) == 1
    # Direct access to A's rows by id answers "not found", for reads and writes alike.
    attempts = [
        ("get", f"/api/applications/{app_id}", None),
        ("post", f"/api/applications/{app_id}/approve", {}),
        ("post", f"/api/applications/{app_id}/reject", {}),
        ("patch", f"/api/cvs/{cv['id']}", {"label": "stolen"}),
        ("get", f"/api/cvs/{cv['id']}/profile", None),
        ("get", f"/api/cv-versions/{cv['current_version_id']}/file", None),
        ("patch", f"/api/campaigns/{a.campaign_id}", {"state": "paused"}),
        ("post", f"/api/campaigns/{a.campaign_id}/run?kind=dispatch", None),
        ("post", f"/api/matches/{match_id}/apply-anyway", None),
        ("delete", f"/api/mail/account/{account_id}", None),
        ("delete", f"/api/blocks/{block_id}", None),
        ("patch", f"/api/preferences/{pref_id}?active=false", None),
        ("post", "/api/preferences", {"text": "x", "campaign_id": str(a.campaign_id)}),
    ]
    for method, path, body in attempts:
        r = getattr(client, method)(path, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"
    # And A's data is untouched.
    assert client.get(f"/api/applications/{app_id}", headers=a.headers).json()["application"]["status"] == "awaiting_approval"
    assert client.get("/api/cvs", headers=a.headers).json()[0]["label"] == "Backend"


def test_every_data_endpoint_requires_login(client):
    for path in ["/api/applications", "/api/cvs", "/api/campaigns", "/api/dashboard", "/api/fact-sheet", "/api/matches",
                 "/api/notifications", "/api/blocks", "/api/preferences", "/api/setup/status", "/api/mail/account"]:
        assert client.get(path).status_code == 401, path


def test_campaign_validation_through_api(client, llm):
    w = make_world(client, llm)
    r = client.patch(f"/api/campaigns/{w.campaign_id}", headers=w.headers, json={"config": {"daily_quota": 30}})
    assert r.status_code == 422 and "Widen the window" in r.json()["detail"]
    r = client.patch(f"/api/campaigns/{w.campaign_id}", headers=w.headers, json={"state": "nonsense"})
    assert r.status_code == 422
    # A second search may not push the user past 30 a day in total.
    r = client.post("/api/campaigns", headers=w.headers, json={"name": "Second", "state": "active",
                    "config": {"daily_quota": 25, "send_window": {"start": "08:00", "end": "12:00"}}})
    assert r.status_code == 422 and "limit per user is 30" in r.json()["detail"]
    r = client.post("/api/campaigns", headers=w.headers, json={"name": "Second", "config": {"daily_quota": 5}, "preferences": ["prefer fintech"]})
    assert r.status_code == 201
    assert client.patch(f"/api/campaigns/{w.campaign_id}", headers=w.headers, json={"approval_mode": "full_auto"}).json()["approval_mode"] == "full_auto"


def test_apply_anyway_overrides_threshold_once(client, llm):
    w = prepared(client, llm)
    skipped = client.get("/api/matches?decision=skip", headers=w.headers).json()
    assert len(skipped) == 1 and "below your threshold" in skipped[0]["reason"] and skipped[0]["company"]
    r = client.post(f"/api/matches/{skipped[0]['id']}/apply-anyway", headers=w.headers)
    assert r.status_code == 200 and r.json()["status"] == "awaiting_approval"
    with w.session() as s:
        app = s.get(Application, __import__("uuid").UUID(r.json()["id"]))
        assert app.override_threshold and s.get(Match, app.match_id).decision == "apply"


def test_apply_anyway_on_a_web_form_job_lists_it_to_apply_by_hand(client, llm):
    import uuid

    from app.models import Posting

    w = prepared(client, llm)
    skipped = client.get("/api/matches?decision=skip", headers=w.headers).json()
    with w.session() as s:
        match = s.get(Match, uuid.UUID(skipped[0]["id"]))
        s.get(Posting, match.posting_id).apply_route = "form"
        s.commit()
    r = client.post(f"/api/matches/{skipped[0]['id']}/apply-anyway", headers=w.headers)
    assert r.status_code == 200 and r.json()["status"] == "manual_apply" and r.json()["route"] == "form"
    with w.session() as s:
        assert s.get(Match, uuid.UUID(skipped[0]["id"])).decision == "manual"


def test_approve_with_edit_and_reject_flow(client, llm):
    w = prepared(client, llm)
    drafts = client.get("/api/applications?status=awaiting_approval", headers=w.headers).json()
    bad = client.post(f"/api/applications/{drafts[0]['id']}/approve", headers=w.headers,
                      json={"body": drafts[0]["body"] + " I also have 20 years of COBOL experience."})
    assert bad.status_code == 409 and "unsupported claim" in bad.json()["detail"]
    good = client.post(f"/api/applications/{drafts[0]['id']}/approve", headers=w.headers, json={"body": drafts[0]["body"]})
    assert good.status_code == 200 and good.json()["status"] == "queued"
    rej = client.post(f"/api/applications/{drafts[1]['id']}/reject", headers=w.headers, json={"reason": "too far", "never_company": True})
    assert rej.json()["status"] == "skipped" and rej.json()["skip_reason"] == "too far"
    assert len(client.get("/api/blocks", headers=w.headers).json()) == 1
    assert client.post(f"/api/applications/{drafts[1]['id']}/approve", headers=w.headers, json={}).status_code == 409


def test_fact_sheet_and_profile_edit(client, llm):
    w = make_world(client, llm)
    saved = client.put("/api/fact-sheet", headers=w.headers, json={"notice_period": "2 months", "hacker_field": "x"}).json()
    assert saved["notice_period"] == "2 months" and "hacker_field" not in saved
    cv = client.get("/api/cvs", headers=w.headers).json()[0]
    profile = client.get(f"/api/cvs/{cv['id']}/profile", headers=w.headers).json()["profile"]
    profile["skills"].append({"name": "terraform"})
    patched = client.patch(f"/api/cvs/{cv['id']}", headers=w.headers, json={"profile": profile}).json()
    assert patched["current_version_id"] != cv["current_version_id"]      # a correction is a new version
    f = client.get(f"/api/cv-versions/{patched['current_version_id']}/file", headers=w.headers)
    assert f.status_code == 200 and b"Alex Example" in f.content


def test_search_and_filters_on_log(client, llm):
    w = prepared(client, llm)
    assert len(client.get("/api/applications?q=acme", headers=w.headers).json()) == 1
    assert len(client.get("/api/applications?status=manual_apply", headers=w.headers).json()) == 1
    assert client.get(f"/api/applications?campaign_id={w.campaign_id}", headers=w.headers).json()


def test_run_now_endpoint(client, llm):
    w = make_world(client, llm)
    discovered(w, llm)
    r = client.post(f"/api/campaigns/{w.campaign_id}/run?kind=prepare", headers=w.headers)
    assert r.status_code == 200 and r.json()["steps"] > 0
    assert client.post(f"/api/campaigns/{w.campaign_id}/run?kind=bogus", headers=w.headers).status_code == 422


def test_unremovable_block(client, llm):
    w = make_world(client, llm)
    from app.models import Block

    with w.session() as s:
        s.add(Block(user_id=w.user_id, type="domain", value="optout.at", reason="asked not to be contacted", removable=False))
    block = client.get("/api/blocks", headers=w.headers).json()[0]
    assert client.delete(f"/api/blocks/{block['id']}", headers=w.headers).status_code == 409


def test_cv_list_names_the_file_in_use(client, llm):
    from tests.helpers import make_world

    w = make_world(client, llm)
    cvs = client.get("/api/cvs", headers=w.headers).json()
    assert cvs and cvs[0]["file_name"] and cvs[0]["uploaded_at"]
