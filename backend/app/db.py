"""Database engine, sessions and tenant isolation.

Every request or task works inside a session bound to one user. The session sets
`app.user_id` in Postgres so row-level security filters every query to that user.
System work that must look across users (scheduling, place allocation) uses
`system_session`, which sets `app.bypass_rls`.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests after changing settings)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def _apply_context(session: Session, user_id: uuid.UUID | None, bypass: bool) -> None:
    """Set the tenant context at the start of every transaction on this session."""

    @event.listens_for(session, "after_begin")
    def _after_begin(sess, transaction, connection):  # noqa: ANN001
        connection.execute(
            text("select set_config('app.user_id', :uid, true), set_config('app.bypass_rls', :b, true)"),
            {"uid": str(user_id) if user_id else "", "b": "on" if bypass else "off"},
        )

    session.info["user_id"] = user_id
    session.info["bypass"] = bypass


@contextmanager
def user_session(user_id: uuid.UUID) -> Iterator[Session]:
    """A session that can only see and change one user's rows."""
    get_engine()
    session: Session = _SessionLocal()
    _apply_context(session, user_id, bypass=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def system_session() -> Iterator[Session]:
    """A session for system work across users. Use sparingly and never from request handlers
    that return data to a user."""
    get_engine()
    session: Session = _SessionLocal()
    _apply_context(session, None, bypass=True)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
