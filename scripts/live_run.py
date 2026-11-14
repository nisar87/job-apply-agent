"""A first real run of the whole application: real Gemini, real job sources, practice mailbox.

Run from the `backend` folder with MAIL_PROVIDER=fake:

    python ../scripts/live_run.py

It wipes the database it is pointed at, then goes through sign-up, CV upload, the setup
chat, a job search on live sources, scoring, drafting, approval, sending (to the practice
mailbox: nothing leaves this computer) and three employer replies. Cost: a few cents.
"""

import json
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402
from try_gemini import SAMPLE_CV  # noqa: E402

from app.config import get_settings  # noqa: E402

FAILS: list[str] = []


def show(label, value, limit=1500):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    print(f"       {label}: {text[:limit]}{' ...' if len(text) > limit else ''}")


def step(name):
    def wrap(fn):
        start = time.time()
        print(f"\n=== {name}")
        try:
            out = fn()
            print(f"[ok]   {name} ({time.time() - start:.1f}s)")
            return out
        except Exception:  # noqa: BLE001
            FAILS.append(name)
            print(f"[FAIL] {name} ({time.time() - start:.1f}s)\n{traceback.format_exc()[-1200:]}")
            return None
    return wrap


def main() -> int:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    s = get_settings()
    if s.mail_provider != "fake":
        print("Refusing to run: set MAIL_PROVIDER=fake so nothing can be sent to a real employer.")
        return 1
    print(f"Provider: {s.llm_provider}; mail: {s.mail_provider}; database: {s.database_url.rsplit('@', 1)[-1]}")

    # ---- 1. Live job sources, called directly ----
    from app.connectors.sources import (
        ArbeitnowSource,
        GreenhouseSource,
        LeverSource,
        PersonioSource,
        SearchQuery,
    )

    def probe(source, slugs, keywords):
        def run():
            for slug in slugs or [None]:
                q = SearchQuery(keywords=keywords, company_slugs=[slug] if slug else [])
                rows = source.search(q)
                dated = sum(1 for r in rows if r.posted_date)
                avg = sum(len(r.description) for r in rows) // max(1, len(rows))
                print(f"       {source.name} {slug or ''}: {len(rows)} postings, {dated} dated, average text {avg} characters")
                for r in rows[:2]:
                    print(f"         - {r.title[:60]} | {r.company[:30]} | {r.location[:30]} | {r.posted_date} | {r.url[:70]}")
        return run

    step("source: Arbeitnow")(probe(ArbeitnowSource(), None, ["python"]))
    step("source: Greenhouse")(probe(GreenhouseSource(), ["gitlab", "n26", "celonis"], ["engineer"]))
    step("source: Lever")(probe(LeverSource(), ["leverdemo", "spotify", "palantir"], ["engineer"]))
    step("source: Personio")(probe(PersonioSource(), ["personio", "n8n", "tado"], []))

    # ---- 2. A clean database and the API in this process ----
    from app.schema import create_schema
    from app.tasks.locks import get_redis

    if step("create a clean database")(lambda: (create_schema(drop_first=True), get_redis().flushdb())) is None:
        return 1
    from app.api.main import app

    client = TestClient(app)
    email = "live-test@example.com"

    def signup():
        r = client.post("/api/auth/signup", json={"email": email, "password": "correct horse battery", "name": "Maria Huber"})
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    h = step("sign up")(signup)
    if h is None:
        return 1
    user_id = client.get("/api/auth/me", headers=h).json()["id"]

    def call(method, path, expect=(200, 201), **kw):
        r = getattr(client, method)(path, headers=h, **kw)
        assert r.status_code in expect, f"{method} {path} -> {r.status_code}: {r.text[:600]}"
        return r.json() if r.content else None

    step("connect the practice mailbox")(lambda: call("post", "/api/mail/fake/connect", json={"address": email}))

    def upload():
        cv = call("post", "/api/cvs", files={"file": ("cv.txt", SAMPLE_CV.encode(), "text/plain")},
                  data={"label": "Backend", "keywords": "python, backend"})
        profile = call("get", f"/api/cvs/{cv['id']}/profile")["profile"]
        show("profile", profile, 900)
        return cv

    step("upload the CV (Gemini reads it)")(upload)
    step("save the fact sheet")(lambda: call("put", "/api/fact-sheet", json={
        "notice_period": "1 month", "work_authorisation": {"at": "citizen", "de": "citizen"},
        "earliest_start": "1 January 2027", "location": "Vienna"}))

    def setup_chat():
        history = [{"role": "user", "text": (
            "I want remote Python backend jobs in Germany or Austria, posted in the last month. Apply to at most "
            "5 a day between 9 and 12, skip recruitment agencies, and never apply to the same company twice within "
            "20 days. Send important replies to maria.private@example.com.")}]
        out = call("post", "/api/setup/turn", json={"history": history, "draft": None})
        show("agent reply", out["reply"])
        show("settings understood", out["config"])
        show("preferences", out["preferences"])
        show("ready", out["ready"])
        return out

    setup = step("setup chat (plain-language instructions)")(setup_chat) or {}

    def make_campaign():
        config = {**(setup.get("config") or {}), "keywords": ["python"], "countries": ["de"], "sources": ["arbeitnow"],
                  "max_posting_age_days": 30}
        c = call("post", "/api/campaigns", json={"name": "Live test", "state": "active", "config": config,
                                                  "preferences": setup.get("preferences") or []})
        show("campaign config", c.get("config"), 700)
        return c

    campaign = step("create the search")(make_campaign)
    if campaign is None:
        return 1
    cid = campaign["id"]

    disc = step("look for jobs on Arbeitnow (Gemini reads each posting)")(
        lambda: call("post", f"/api/campaigns/{cid}/run?kind=discovery"))
    show("result", disc)

    def prepare():
        out = call("post", f"/api/campaigns/{cid}/run?kind=prepare")
        show("outcome", {k: out.get(k) for k in ("summary", "stop_reason")})
        show("steps", out.get("steps"), 2500)
        return out

    step("score and write drafts (the agent)")(prepare)

    def overview():
        matches = call("get", "/api/matches?decision=skip,apply,manual")
        by = {}
        for m in matches:
            by.setdefault(m["decision"], []).append(m)
        print(f"       matches: { {k: len(v) for k, v in by.items()} }")
        for m in matches[:25]:
            print(f"         - {m['decision']:6} {str(m.get('total_score')):5} {m['title'][:45]:45} | {m['company'][:22]:22} | {str(m.get('reason'))[:90]}")
        apps = call("get", "/api/applications")
        print(f"       applications: { {st: sum(1 for a in apps if a['status'] == st) for st in {a['status'] for a in apps}} }")
        return apps

    apps = step("what the agent decided")(overview) or []
    drafts = [a for a in apps if a["status"] == "awaiting_approval"]
    for a in drafts[:2]:
        print(f"\n       --- draft for {a['title']} at {a['company']} (score {a.get('score')}, to {a.get('to_address') or a.get('recipient')})")
        print(f"       Subject: {a.get('subject')}\n{a.get('body')}\n       ---")

    def approve():
        done = []
        for a in drafts[:3]:
            r = client.post(f"/api/applications/{a['id']}/approve", headers=h, json={})
            print(f"       approve {a['company'][:30]}: {r.status_code} {r.json().get('status') or r.text[:200]}")
            if r.status_code == 200:
                done.append(a["id"])
        assert done or not drafts, "no draft could be approved"
        return done

    approved = step("approve up to three drafts")(approve) or []

    def send():
        import uuid as _uuid

        from app.agent import runs as agent_runs
        from app.db import user_session

        day = datetime.now(UTC) + timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        results = []
        for minute in range(5, 60, 10):
            now = day.replace(hour=9, minute=minute, second=0, microsecond=0)
            with user_session(_uuid.UUID(user_id)) as session:
                results.append(agent_runs.dispatch_run(session, _uuid.UUID(user_id), now))
        show("dispatch results", results, 1500)
        sent = [a for a in call("get", "/api/applications") if a["status"] == "sent"]
        print(f"       sent to the practice mailbox: {len(sent)}")
        return sent

    sent = step("send in the send window (practice mailbox)")(send) or []

    replies = [
        "Thank you for your application. We have received it and will get back to you.",
        "Thanks for applying. Could you tell us your notice period and when you could start?",
        "We would like to invite you to a video interview on Tuesday at 14:00 CET. Please confirm.",
    ]

    def employer_replies():
        import uuid as _uuid

        from app.connectors.mail import get_mail_provider
        from app.db import user_session
        from app.models import Application

        mail = get_mail_provider()
        for a, body in zip(sent, replies, strict=False):
            with user_session(_uuid.UUID(user_id)) as session:
                row = session.get(Application, _uuid.UUID(a["id"]))
                thread, to = row.thread_id, mail.threads[row.thread_id][0].to
            mail.deliver(email, sender=to, body=body, thread_id=thread, subject="Re: " + (a.get("subject") or ""))
        out = call("post", "/api/inbox/sync")
        show("inbox run", out)
        for a in sent[:3]:
            d = call("get", f"/api/applications/{a['id']}")
            print(f"       {a['company'][:30]}: status {d['application']['status']}")
            for m in d["messages"]:
                print(f"         {m['direction']:3} [{m.get('category')}] by {m.get('handled_by')}: {str(m['body'])[:160]!r}")
        esc = call("get", "/api/escalations")
        for e in esc:
            print(f"       escalation ({e['urgency']}): {e['reason'][:120]} | suggested: {str(e.get('suggested_reply'))[:200]!r}")
        return out

    if sent:
        step("three employer replies (Gemini sorts and answers them)")(employer_replies)
    else:
        print("\n(no application was sent, so the reply step was skipped)")

    step("dashboard and cost")(lambda: (show("dashboard", call("get", "/api/dashboard"), 1200),
                                        show("health", call("get", "/api/stats/daily"), 600)))
    print("\nFailed steps:", FAILS or "none")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
