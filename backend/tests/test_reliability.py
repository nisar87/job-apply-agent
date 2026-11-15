"""Failures in one place must never cause a second email to an employer, unlimited model spend, a global
block on a free-mail provider, or an approved draft expiring before it can be sent."""

from datetime import timedelta

from sqlalchemy import select

from app.agent import runs as agent_runs
from app.agent.loop import run_llm_agent
from app.agent.tools import AgentContext, call_tool
from app.llm.base import SpendCapExceeded
from app.models import Application, Block, GlobalBlock, Message, Run, User
from app.services import applications as app_service
from app.services import inbox
from tests.helpers import NOW, campaign_of, make_world
from tests.test_phase1_agent import POSTINGS, discovered
from tests.test_phase1_rules import queued_app
from tests.test_phase2 import sent_world


def test_a_failure_later_in_the_inbox_pass_never_resends_an_answer(client, llm):
    w = sent_world(client, llm)
    base_classify = llm.handlers["classify_reply"]

    def classify(prompt, schema):
        if "explode" in prompt.split("<newest_email", 1)[1]:
            raise RuntimeError("model unavailable")
        return base_classify(prompt, schema)

    llm.on("classify_reply", classify)
    when = NOW + timedelta(hours=1)
    w.mail.deliver("a@example.com", sender="jobs@acme.at", body="Thanks. What is your notice period?", thread_id=w.thread_id, when=when)
    w.mail.deliver("a@example.com", sender="jobs@acme.at", body="Also, explode please", thread_id=w.thread_id,
                   when=when + timedelta(minutes=1))
    with w.session() as s:
        before = app_service.mail_account(s, w.user_id).last_history_id
        out = inbox.sync_inbox(s, llm, w.user_id, when + timedelta(minutes=2))
        after = app_service.mail_account(s, w.user_id).last_history_id
    assert out["handled"] == 1 and out["retry_next_pass"] is True
    assert after == before            # the read position stays, so the failed email is listed again next time
    with w.session() as s:            # the answer that was sent is on record for good
        stored = s.execute(select(Message).where(Message.direction == "in")).scalars().all()
    assert [m.category for m in stored] == ["routine_question"]
    # Seeing the same emails again (as a re-read from the old position does) never answers twice.
    llm.on("classify_reply", base_classify)
    for m in list(w.mail.threads[w.thread_id]):
        if m.sender == "jobs@acme.at":
            w.mail.pending.setdefault("a@example.com", []).append(m)
    with w.session() as s:
        inbox.sync_inbox(s, llm, w.user_id, when + timedelta(minutes=10))
        stored = s.execute(select(Message).where(Message.direction == "in")).scalars().all()
    assert len([m for _, m, _ in w.mail.sent if m.body == "My notice period is 1 month."]) == 1
    assert len(stored) == 2           # the failed one was handled on the retry


def test_the_spend_cap_inside_a_tool_stops_the_run_and_keeps_its_drafts(client, llm):
    w = make_world(client, llm)
    discovered(w, llm, POSTINGS)
    wrote = {"n": 0}
    base_score, base_write = llm.handlers["score_job"], llm.handlers["write_application"]

    def write(prompt, schema):
        if wrote["n"]:               # the cap is reached while writing the second draft
            raise SpendCapExceeded("daily model spend cap reached")
        wrote["n"] += 1
        return base_write(prompt, schema)

    llm.on("score_job", base_score)
    llm.on("write_application", write)
    with w.session() as s:
        out = agent_runs.prepare_run(s, llm, campaign_of(s, w), NOW)
        drafts = s.execute(select(Application)).scalars().all()
        run = s.execute(select(Run).where(Run.type == "prepare")).scalar_one()
    assert out["stop_reason"] == "spend cap" and run.state == "stopped"
    assert len(drafts) == 1                         # the draft written before the cap is kept


def test_a_failing_tool_is_contained_and_the_run_goes_on(client, llm):
    w = make_world(client, llm)
    discovered(w, llm, POSTINGS)
    base_score = llm.handlers["score_job"]
    llm.on("score_job", lambda p, s: (_ for _ in ()).throw(RuntimeError("provider error")) if "Platform Engineer" in p
           else base_score(p, s))
    with w.session() as s:
        ctx = AgentContext(session=s, llm=llm, campaign=campaign_of(s, w), user=s.get(User, w.user_id), now=NOW)
        ids = [c["posting_id"] for c in call_tool(ctx, "list_candidates", {})["candidates"]]
        results = [call_tool(ctx, "score_job", {"posting_id": i}) for i in ids]
    assert any("failed (RuntimeError)" in r.get("error", "") for r in results)
    assert any("decision" in r for r in results)


def test_a_failed_run_is_recorded_so_it_is_not_retried_every_tick(client, llm):
    w = make_world(client, llm)
    llm.on("agent_prepare", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down")))
    try:
        with w.session() as s:
            agent_runs.prepare_run(s, llm, campaign_of(s, w), NOW)
    except RuntimeError:
        pass
    with w.session() as s:
        runs = s.execute(select(Run).where(Run.type == "prepare")).scalars().all()
    assert len(runs) == 1 and runs[0].state == "failed" and "model down" in runs[0].summary


def test_do_not_contact_from_a_free_mail_address_blocks_only_that_address(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm, title="Recruiter role", company="Solo Recruiting", email="jane.recruits@gmail.com")
        app.recipient = "jane.recruits@gmail.com"
        inbox._do_not_contact(s, app)
        blocks = {(b.type, b.value) for b in s.execute(select(Block)).scalars()}
    from app.db import system_session

    with system_session() as sys:
        global_blocks = {(b.type, b.value) for b in sys.execute(select(GlobalBlock)).scalars()}
    assert ("address", "jane.recruits@gmail.com") in blocks and ("domain", "gmail.com") not in blocks
    assert ("domain", "gmail.com") not in global_blocks


def test_approving_keeps_a_draft_alive_until_it_can_be_sent(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm, approve=False)
        app.expires_at = NOW + timedelta(hours=2)
        app_service.approve(s, app)
        assert app.expires_at >= app.approved_at + timedelta(days=app_service.APPROVED_VALID_DAYS)


def test_llm_agent_reports_the_spend_cap(client, llm):
    w = make_world(client, llm)
    discovered(w, llm, POSTINGS)
    llm.on("score_job", lambda p, s: (_ for _ in ()).throw(SpendCapExceeded("cap")))
    with w.session() as s:
        ctx = AgentContext(session=s, llm=llm, campaign=campaign_of(s, w), user=s.get(User, w.user_id), now=NOW)
        out = run_llm_agent(ctx)
    assert out["stop_reason"] == "spend cap"
