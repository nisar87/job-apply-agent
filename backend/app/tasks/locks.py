"""Per-user run locks and idempotency keys, backed by Redis."""

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import redis

from app.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


class LockBusy(Exception):
    """Another run of the same type is already in progress for this user."""


@contextmanager
def run_lock(user_id: uuid.UUID, run_type: str, ttl_seconds: int = 900) -> Iterator[None]:
    """One run per user per run type at a time (master document, section 4)."""
    key = f"lock:{run_type}:{user_id}"
    token = uuid.uuid4().hex
    r = get_redis()
    if not r.set(key, token, nx=True, ex=ttl_seconds):
        raise LockBusy(key)
    try:
        yield
    finally:
        # Release only our own lock.
        if r.get(key) == token:
            r.delete(key)


def idempotency_key(*parts: object) -> str:
    """A stable key for an action, so a retry can never repeat it."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
