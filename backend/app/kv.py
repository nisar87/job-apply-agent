"""A Postgres stand-in for the few Redis commands the application uses.

Small deployments (one Cloud Run service, no Celery workers) can then run without a Redis server:
set REDIS_URL=postgres. Supported: get, set (with nx and ex), delete, incr, expire, ping, flushdb.
Each call is its own short transaction, so locks and counters behave as they do in Redis.
"""

import random

from sqlalchemy import text

from app.db import get_engine

_EXPIRED = "(expires_at IS NOT NULL AND expires_at <= now())"


class PostgresKV:
    def _run(self, sql: str, **params):
        with get_engine().begin() as conn:
            return conn.execute(text(sql), params)

    def ping(self) -> bool:
        self._run("SELECT 1")
        return True

    def get(self, key: str) -> str | None:
        row = self._run(f"SELECT value FROM kv_store WHERE key = :k AND NOT {_EXPIRED}", k=key).first()
        return row[0] if row else None

    def set(self, key: str, value, nx: bool = False, ex: int | None = None) -> bool | None:
        """Like Redis SET. With nx, only when the key is missing (or expired); returns None otherwise."""
        if random.random() < 0.01:
            self._run(f"DELETE FROM kv_store WHERE {_EXPIRED}")
        expires = "now() + make_interval(secs => :ex)" if ex else "NULL"
        guard = f"WHERE {_EXPIRED.replace('expires_at', 'kv_store.expires_at')}" if nx else ""
        row = self._run(
            f"INSERT INTO kv_store (key, value, expires_at) VALUES (:k, :v, {expires}) "
            f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at {guard} "
            "RETURNING key",
            k=key, v=str(value), ex=ex or 0,
        ).first()
        return True if row else None

    def hset(self, name: str, field: str, value) -> int:
        """Like Redis HSET: one field of a named hash, kept as its own row."""
        self.set(f"{name}#{field}", value)
        return 1

    def hgetall(self, name: str) -> dict[str, str]:
        rows = self._run(f"SELECT key, value FROM kv_store WHERE key LIKE :p AND NOT {_EXPIRED}", p=f"{name}#%").all()
        return {k[len(name) + 1:]: v for k, v in rows}

    def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return self._run("DELETE FROM kv_store WHERE key = ANY(:ks)", ks=list(keys)).rowcount

    def incr(self, key: str) -> int:
        """Like Redis INCR: a missing or expired key starts again at 1, without an expiry."""
        row = self._run(
            "INSERT INTO kv_store (key, value, expires_at) VALUES (:k, '1', NULL) "
            "ON CONFLICT (key) DO UPDATE SET "
            f"value = CASE WHEN {_EXPIRED.replace('expires_at', 'kv_store.expires_at')} THEN '1' "
            "ELSE (kv_store.value::bigint + 1)::text END, "
            f"expires_at = CASE WHEN {_EXPIRED.replace('expires_at', 'kv_store.expires_at')} THEN NULL "
            "ELSE kv_store.expires_at END "
            "RETURNING value",
            k=key,
        ).first()
        return int(row[0])

    def expire(self, key: str, seconds: int) -> bool:
        res = self._run(
            f"UPDATE kv_store SET expires_at = now() + make_interval(secs => :s) WHERE key = :k AND NOT {_EXPIRED}",
            k=key, s=seconds,
        )
        return res.rowcount > 0

    def flushdb(self) -> bool:
        self._run("DELETE FROM kv_store")
        return True
