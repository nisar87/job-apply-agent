"""The Postgres stand-in behaves like Redis for every command the application uses."""

import time

from app.kv import PostgresKV


def test_postgres_kv_matches_redis_semantics():
    kv = PostgresKV()
    kv.flushdb()
    assert kv.ping() is True
    assert kv.get("a") is None
    assert kv.set("a", "1") is True and kv.get("a") == "1"
    # nx: only when missing; a held lock is not taken twice.
    assert kv.set("lock", "me", nx=True, ex=60) is True
    assert kv.set("lock", "you", nx=True, ex=60) is None and kv.get("lock") == "me"
    # Counters with an expiry, as the sign-in limit uses them.
    assert kv.incr("n") == 1 and kv.incr("n") == 2
    assert kv.expire("n", 60) is True and kv.incr("n") == 3
    assert kv.expire("missing", 60) is False
    assert kv.delete("a", "n") == 2 and kv.get("a") is None and kv.get("n") is None
    # Expired keys read as missing, can be taken with nx, and restart counters at 1.
    kv.set("short", "x", ex=1)
    kv.set("count", "5")
    kv.expire("count", 1)
    time.sleep(1.2)
    assert kv.get("short") is None
    assert kv.set("short", "y", nx=True) is True and kv.get("short") == "y"
    assert kv.incr("count") == 1
    assert kv.flushdb() is True and kv.get("short") is None
