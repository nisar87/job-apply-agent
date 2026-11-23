"""Phase 6: one queued job per user; a failing or stuck user never delays the others."""

import uuid

from app.tasks import jobs, scheduler
from app.tasks.locks import get_redis, run_lock


def test_a_failing_user_does_not_stop_the_others(monkeypatch):
    users = [uuid.uuid4() for _ in range(20)]
    monkeypatch.setattr(scheduler, "_active_users", lambda: users)
    monkeypatch.setattr(scheduler.harvest if hasattr(scheduler, "harvest") else __import__("app.services.harvest", fromlist=["x"]),
                        "start_if_due", lambda now: False)
    get_redis().delete("global_pause")
    done = []

    def fake_tick_user(uid, now=None, fetcher=None, **kw):
        if uid == users[3]:
            raise RuntimeError("boom")
        done.append(uid)
        return {"sent": 1, "errors": []}

    monkeypatch.setattr(scheduler, "tick_user", fake_tick_user)
    summary = scheduler.tick()
    assert summary["users"] == 20 and summary["sent"] == 19 and summary["failed"] == 1
    assert len(done) == 19 and users[3] not in done
    health = scheduler.health()
    assert "boom" in health[str(users[3])]["errors"][0] and health[str(users[0])]["errors"] == []


def test_a_second_job_for_a_user_already_running_does_nothing(monkeypatch):
    uid = uuid.uuid4()
    called = []
    monkeypatch.setattr(scheduler, "tick_user", lambda *a, **k: called.append(1) or {"sent": 0, "errors": []})
    with run_lock(uid, "tick"):
        assert jobs.tick_user_task.run(str(uid)) == {"busy": True, "sent": 0}
    assert called == []


def test_the_database_backed_store_keeps_the_background_health(client):
    """Staging has no Redis: the health note must work on the database store too."""
    from app.kv import PostgresKV
    kv = PostgresKV()
    kv.hset("health:test", "u1", '{"a": 1}')
    kv.hset("health:test", "u2", '{"a": 2}')
    assert kv.hgetall("health:test") == {"u1": '{"a": 1}', "u2": '{"a": 2}'}
    kv.delete("health:test#u1", "health:test#u2")
