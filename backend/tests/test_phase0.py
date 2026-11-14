import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db import system_session, user_session
from app.llm import MeteredLLM, SpendCapExceeded
from app.models import AuditLog, Run, UsageDaily, User
from app.security import decrypt_secret, encrypt_secret
from app.tasks.locks import LockBusy, idempotency_key, run_lock
from tests.conftest import make_user


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True


def test_signup_login_me(client):
    headers, user_id = make_user(client)
    assert client.get("/api/auth/me", headers=headers).json()["email"] == "a@example.com"
    r = client.post("/api/auth/login", json={"email": "A@example.com", "password": "correct horse battery"})
    assert r.status_code == 200
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong password!"}).status_code == 401
    assert client.post("/api/auth/signup", json={"email": "a@example.com", "password": "another password"}).status_code == 409
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_short_password_rejected(client):
    assert client.post("/api/auth/signup", json={"email": "c@example.com", "password": "short"}).status_code == 422


def test_ping_task_calls_model_and_writes_audit(client, user, llm):
    headers, user_id = user
    llm.on("ping", lambda prompt, schema: "pong")
    r = client.post("/api/system/ping-task", headers=headers)
    assert r.status_code == 200 and r.json()["result"]["text"] == "pong"
    with user_session(uuid.UUID(user_id)) as s:
        rows = s.execute(select(AuditLog).where(AuditLog.tool == "ping_llm")).scalars().all()
        assert len(rows) == 1
        run = s.execute(select(Run)).scalar_one()
        assert run.state == "done"
        usage = s.execute(select(UsageDaily)).scalar_one()
        assert usage.calls == 1 and usage.input_tokens > 0


def test_row_level_security_isolates_users(client, user, other_user):
    (_, a), (_, b) = user, other_user
    a, b = uuid.UUID(a), uuid.UUID(b)
    with user_session(a) as s:
        # A sees only A's own row and A's own audit entries.
        assert [u.id for u in s.execute(select(User)).scalars()] == [a]
        assert {r.user_id for r in s.execute(select(AuditLog)).scalars()} == {a}
        # A cannot write a row for B.
        s.add(AuditLog(user_id=b, actor="user", tool="forged"))
        with pytest.raises(DBAPIError):
            s.flush()
        s.rollback()
    with user_session(b) as s:
        assert s.execute(select(AuditLog).where(AuditLog.tool == "forged")).first() is None
    with system_session() as s:
        assert len(s.execute(select(User)).scalars().all()) == 2


def test_context_survives_commit(client, user):
    _, a = user
    with user_session(uuid.UUID(a)) as s:
        s.commit()
        assert s.execute(text("select current_setting('app.user_id', true)")).scalar() == a


def test_run_lock_is_exclusive():
    uid = uuid.uuid4()
    with run_lock(uid, "apply"):
        with pytest.raises(LockBusy):
            with run_lock(uid, "apply"):
                pass
        with run_lock(uid, "inbox"):
            pass  # a different run type is free
    with run_lock(uid, "apply"):
        pass  # released


def test_idempotency_key_is_stable():
    assert idempotency_key("a", 1) == idempotency_key("a", 1)
    assert idempotency_key("a", 1) != idempotency_key("a", 2)


def test_secret_encryption_round_trip():
    token = encrypt_secret("refresh-token-value")
    assert token != "refresh-token-value" and decrypt_secret(token) == "refresh-token-value"


def test_spend_cap_blocks_model_calls(client, user, llm):
    _, a = user
    with user_session(uuid.UUID(a)) as s:
        metered = MeteredLLM(llm, s, uuid.UUID(a), cap_usd=0.01)
        metered.generate("hello")
        usage = s.execute(select(UsageDaily)).scalar_one()
        usage.cost_usd = 0.02
        s.flush()
        with pytest.raises(SpendCapExceeded):
            metered.generate("again")
