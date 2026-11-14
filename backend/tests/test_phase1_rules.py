"""Send rules, idempotent sending, places on a posting, and planning."""

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.mail import MailAuthError
from app.db import system_session
from app.models import Application, AuditLog, Block, Campaign, GlobalBlock, Notification, PostingSlot, User
from app.services import applications as app_service
from app.services import matching, rules, slots
from app.services import postings as posting_service
from app.services.campaigns import get_config
from tests.helpers import NOW, always_ok, campaign_of, make_world, raw


def queued_app(s, w, llm, approve=True, **kw) -> Application:
    """Ingest, score and draft one posting; optionally approve it."""
    posting = posting_service.ingest(s, llm, raw(**kw), mail_check=always_ok)[0]
    campaign = campaign_of(s, w)
    match = matching.evaluate(s, llm, campaign, get_config(campaign), posting, {}, [])
    assert match.decision == "apply", match.reason
    app = app_service.create_draft(s, llm, campaign, match)
    assert app.status == "awaiting_approval", app.check_results
    if approve:
        app_service.approve(s, app)
    return app


def test_send_happy_path(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm)
        app_service.send(s, app, NOW)
        assert app.status == "sent" and app.thread_id and app.sent_at == NOW
        address, mail, _ = w.mail.sent[0]
        assert mail.to == "jobs@acme.at" and mail.attachments[0].name == "cv.txt" and "Acme" in mail.body
        assert s.execute(select(AuditLog).where(AuditLog.tool == "send_application")).scalar_one().entity_id == app.id
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, app, NOW)          # a sent application is never sent again
        assert e.value.code == "not_queued"
    assert len(w.mail.sent) == 1


def test_review_mode_needs_approval(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm, approve=False)
        app.status = "queued"                      # even if something queues it, the rule still refuses
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, app, NOW)
        assert e.value.code == "needs_approval"
    assert not w.mail.sent


def test_recipient_is_never_free_text(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm)
        app.recipient = "attacker@evil.example"
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, app, NOW)
        assert e.value.code == "recipient_mismatch"
    assert not w.mail.sent


def test_send_window_edges_and_timezone(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        user = s.get(User, w.user_id)
        user.timezone = "Europe/Vienna"            # UTC+1 in November: window 09:00-12:00 local = 08:00-11:00 UTC
        app = queued_app(s, w, llm)
        cfg, camp, posting = get_config(campaign_of(s, w)), campaign_of(s, w), s.get(posting_service.Posting, app.posting_id)
        day = datetime(2026, 11, 10, tzinfo=UTC)
        for hour, minute, ok in [(7, 59, False), (8, 0, True), (10, 59, True), (11, 0, False)]:
            now = day.replace(hour=hour, minute=minute)
            if ok:
                rules.check_send(s, app, camp, cfg, user, posting, now)
            else:
                with pytest.raises(rules.RuleViolation) as e:
                    rules.check_send(s, app, camp, cfg, user, posting, now)
                assert e.value.code == "outside_window" and e.value.retry_later
        saturday = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
        with pytest.raises(rules.RuleViolation):
            rules.check_send(s, app, camp, cfg, user, posting, saturday)


def test_daily_quota_boundary(client, llm):
    w = make_world(client, llm, config={"daily_quota": 2, "company_cooldown_days": 0})
    with w.session() as s:
        apps = [queued_app(s, w, llm, title=f"Engineer {i}", company=f"Firm{i} GmbH", email=f"jobs@firm{i}.at") for i in range(3)]
        app_service.send(s, apps[0], NOW)
        app_service.send(s, apps[1], NOW + timedelta(minutes=5))       # the 2nd of 2 is allowed
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, apps[2], NOW + timedelta(minutes=10))  # the 3rd is refused
        assert e.value.code == "campaign_quota"
        # The next day the quota is fresh.
        app_service.send(s, apps[2], NOW + timedelta(days=1))
    assert len(w.mail.sent) == 3


def test_user_ceiling_across_campaigns(client, llm):
    w = make_world(client, llm, config={"company_cooldown_days": 0})
    with w.session() as s:
        s.get(User, w.user_id).daily_send_limit = 1
        a = queued_app(s, w, llm, title="Engineer A", company="A GmbH", email="jobs@a-firm.at")
        b = queued_app(s, w, llm, title="Engineer B", company="B GmbH", email="jobs@b-firm.at")
        app_service.send(s, a, NOW)
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, b, NOW + timedelta(minutes=5))
        assert e.value.code == "user_quota"


def test_minimum_gap_between_sends(client, llm):
    w = make_world(client, llm, config={"company_cooldown_days": 0})
    with w.session() as s:
        a = queued_app(s, w, llm, title="Engineer A", company="A GmbH", email="jobs@a-firm.at")
        b = queued_app(s, w, llm, title="Engineer B", company="B GmbH", email="jobs@b-firm.at")
        app_service.send(s, a, NOW)
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, b, NOW + timedelta(seconds=60))
        assert e.value.code == "too_soon"
        app_service.send(s, b, NOW + timedelta(minutes=2))


def test_company_cooldown_day_15_and_16(client, llm):
    w = make_world(client, llm, config={"contact_cooldown_days": 0})
    with w.session() as s:
        first = queued_app(s, w, llm, title="Backend Engineer")
        app_service.send(s, first, NOW)
        second = queued_app(s, w, llm, title="Platform Engineer", email="careers@acme.at", posted=NOW.date() + timedelta(days=13))
        second.expires_at = NOW + timedelta(days=30)
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, second, NOW + timedelta(days=14))      # inside 15 days (a Tuesday)
        assert e.value.code == "company_cooldown"
        app_service.send(s, second, NOW + timedelta(days=15, minutes=1))  # just past 15 days: allowed
    assert len(w.mail.sent) == 2


def test_contact_cooldown(client, llm):
    w = make_world(client, llm, config={"company_cooldown_days": 0})
    with w.session() as s:
        app_service.send(s, queued_app(s, w, llm, title="Backend Engineer"), NOW)
        again = queued_app(s, w, llm, title="Data Engineer", company="Other GmbH", email="jobs@acme.at")
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, again, NOW + timedelta(minutes=10))
        assert e.value.code == "contact_cooldown"


def test_blocks_apply_at_send_time(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm)
        s.add(Block(user_id=w.user_id, type="domain", value="acme.at"))
        s.flush()
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, app, NOW)
        assert e.value.code == "blocked"
    w2 = make_world(client, llm, email="b@example.com", mail=w.mail)
    with w2.session() as s:
        app_id = queued_app(s, w2, llm, company="Zeta GmbH", email="jobs@zeta.at").id
    with system_session() as s:                 # the employer opts out after the draft was approved
        s.add(GlobalBlock(type="address", value="jobs@zeta.at", reason="asked not to be contacted"))
    with w2.session() as s:
        app = s.get(Application, app_id)
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, app, NOW)
        assert e.value.code == "blocked" and "asked not to be contacted" in e.value.message


def test_paused_and_dry_run(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm)
        campaign_of(s, w).state = "paused"
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, app, NOW)
        assert e.value.code == "campaign_paused"
        campaign_of(s, w).state = "dry_run"
        app_service.send(s, app, NOW)
        assert app.status == "dry_run"
    assert not w.mail.sent


def test_interrupted_send_is_never_repeated(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm)
        w.mail.fail_next = KeyboardInterrupt()       # the process dies inside the provider call
        with pytest.raises(KeyboardInterrupt):
            app_service.send(s, app, NOW)
        app_id = app.id
    with w.session() as s:
        app = s.get(Application, app_id)
        assert app.status == "sending"               # committed before the call, so a retry cannot resend
        with pytest.raises(rules.RuleViolation):
            app_service.send(s, app, NOW + timedelta(minutes=1))
        app.updated_at = NOW - timedelta(minutes=30)
        s.flush()
        assert app_service.recover_stale_sends(s, w.user_id, NOW + timedelta(minutes=30)) == 1
        assert app.status == "failed" and "Sent folder" in app.skip_reason
        assert s.execute(select(Notification).where(Notification.kind == "send_unknown")).scalar_one()
    assert not w.mail.sent


def test_provider_error_marks_failed_and_auth_error_pauses(client, llm):
    w = make_world(client, llm, config={"company_cooldown_days": 0})
    with w.session() as s:
        a = queued_app(s, w, llm, title="Engineer A", company="A GmbH", email="jobs@a-firm.at")
        w.mail.fail_next = RuntimeError("boom")
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, a, NOW)
        assert e.value.code == "send_failed" and a.status == "failed"
        b = queued_app(s, w, llm, title="Engineer B", company="B GmbH", email="jobs@b-firm.at")
        w.mail.fail_next = MailAuthError("revoked")
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, b, NOW)
        assert e.value.code == "mail_auth" and b.status == "queued"
        assert s.get(Campaign, w.campaign_id).state == "paused"
        assert s.execute(select(Notification).where(Notification.kind == "reconnect_mail")).scalar_one()


def test_sandbox_recipient_redirects(client, llm, monkeypatch):
    monkeypatch.setenv("SANDBOX_RECIPIENT", "sandbox@example.com")
    get_settings.cache_clear()
    w = make_world(client, llm)
    with w.session() as s:
        app = queued_app(s, w, llm)
        app_service.send(s, app, NOW)
        assert w.mail.sent[0][1].to == "sandbox@example.com" and app.recipient == "jobs@acme.at"
    get_settings.cache_clear()


def test_draft_expiry_and_reject(client, llm):
    w = make_world(client, llm, config={"company_cooldown_days": 0})
    with w.session() as s:
        a = queued_app(s, w, llm, approve=False, title="Engineer A", company="A GmbH", email="jobs@a-firm.at")
        b = queued_app(s, w, llm, approve=False, title="Engineer B", company="B GmbH", email="jobs@b-firm.at")
        assert app_service.expire_drafts(s, w.user_id, NOW + timedelta(days=2)) == 0
        app_service.reject(s, b, "not for me", never_company=True)
        assert b.status == "skipped" and s.execute(select(Block)).scalar_one().value == "B GmbH"
        assert app_service.expire_drafts(s, w.user_id, datetime.now(UTC) + timedelta(days=4)) == 1
        assert a.status == "skipped" and a.skip_reason == "not approved in time"


def test_failed_checks_block_sending_and_edit_fixes(client, llm):
    w = make_world(client, llm)
    llm.bad_claim = True
    with w.session() as s:
        posting = posting_service.ingest(s, llm, raw(), mail_check=always_ok)[0]
        campaign = campaign_of(s, w)
        match = matching.evaluate(s, llm, campaign, get_config(campaign), posting, {}, [])
        app = app_service.create_draft(s, llm, campaign, match)
        assert app.status == "failed" and "unsupported claim" in app.skip_reason and app.check_results.get("rewritten")
        with pytest.raises(app_service.ApplicationError):
            app_service.approve(s, app)
        fixed = app.body.replace(" I also have 20 years of COBOL experience.", "")
        app_service.approve(s, app, body=fixed, llm=llm)
        assert app.status == "queued"


def test_rewrite_once_recovers(client, llm):
    w = make_world(client, llm)
    llm.bad_claim_once = True
    with w.session() as s:
        app = queued_app(s, w, llm)
        assert app.check_results["passed"] and app.check_results.get("rewritten")


def test_plan_send_times_fits_window():
    rng = random.Random(7)
    end = NOW + timedelta(minutes=180)
    times = rules.plan_send_times(20, NOW, end, rng)
    assert len(times) == 20 and all(NOW < t < end for t in times) and times == sorted(times)
    tight = rules.plan_send_times(20, NOW, NOW + timedelta(minutes=60), random.Random(7))
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(tight, tight[1:], strict=False)]
    assert all(g >= 2 - 1e-6 for g in gaps) and all(t < NOW + timedelta(minutes=60) for t in tight)
    # 20 sends cannot fit 20 minutes at a 2-minute minimum: fewer are planned.
    assert len(rules.plan_send_times(20, NOW, NOW + timedelta(minutes=20), random.Random(7))) < 20
    assert rules.plan_send_times(5, NOW, NOW) == []


def test_posting_places_cap_per_day_and_total(client, llm):
    mail = None
    worlds = []
    for i in range(7):
        w = make_world(client, llm, email=f"u{i}@example.com", mail=mail)
        mail = w.mail
        worlds.append(w)
    results = []
    for i, w in enumerate(worlds):
        with w.session() as s:
            app = queued_app(s, w, llm)
            try:
                app_service.send(s, app, NOW + timedelta(seconds=i))
                results.append("sent")
            except rules.RuleViolation as v:
                results.append(v.code)
    assert results == ["sent"] * 5 + ["employer_limit"] * 2          # 5 users a day per posting
    with system_session() as s:
        slot = s.execute(select(PostingSlot)).scalars().first()
        assert slot.sent == 5
        slot.sent = 20                                                # simulate the total cap being reached
    with worlds[5].session() as s:
        app = s.execute(select(Application)).scalar_one()
        with pytest.raises(rules.RuleViolation) as e:
            app_service.send(s, app, NOW + timedelta(days=1))
        assert e.value.code == "employer_limit"


def test_allocation_uses_neutral_score_and_releases(client, llm):
    mail, worlds = None, []
    for i in range(7):
        w = make_world(client, llm, email=f"u{i}@example.com", mail=mail)
        mail = w.mail
        worlds.append(w)
        with w.session() as s:
            app = queued_app(s, w, llm, approve=False)
            app.neutral_score = 50 + i          # user 6 has the best neutral score
            app.score = 100 - i                 # personal scores run the other way and must not matter
    day = NOW.date()
    with system_session() as s:
        winners = slots.allocate(s, day, random.Random(1))
        ids = next(iter(winners.values()))
        assert len(ids) == 5
        holders = {a.user_id for a in s.execute(select(Application).where(Application.place_day == day)).scalars()}
        assert holders == {w.user_id for w in worlds[2:]}            # the five highest neutral scores
    with worlds[0].session() as s:                                    # a loser cannot take a place
        app = s.execute(select(Application)).scalar_one()
        assert not slots.has_or_take_place(s, app, day)
    with worlds[6].session() as s:                                    # a winner rejects: the place returns
        app_service.reject(s, s.execute(select(Application)).scalar_one())
    with worlds[0].session() as s:
        app = s.execute(select(Application)).scalar_one()
        assert slots.has_or_take_place(s, app, day)
