"""Which users' applications really reach employers.

In production everyone's do. On the test site everything goes to the sandbox inbox and only practice forms are
submitted, except for users the admin has switched to real sending (Admin → Users → Real sending). That lets
the owner use the test site for their own real job hunt while every other test account stays sandboxed.
"""

import json
import uuid

from app.config import get_settings

KEY = "platform:real_send"


def _kv():
    from app.tasks.locks import get_redis

    return get_redis()


def real_users() -> set[str]:
    try:
        return set(json.loads(_kv().get(KEY) or "[]"))
    except (ValueError, TypeError):
        return set()


def set_real(user_id: uuid.UUID, on: bool) -> None:
    users = real_users()
    (users.add if on else users.discard)(str(user_id))
    _kv().set(KEY, json.dumps(sorted(users)))


def sends_for_real(user_id: uuid.UUID | None) -> bool:
    if get_settings().is_production:
        return True
    return user_id is not None and str(user_id) in real_users()


PROOF_KEY = "platform:proof_review"
PROOF_DEFAULT = 30


def proof_map() -> dict:
    try:
        return dict(json.loads(_kv().get(PROOF_KEY) or "{}"))
    except (ValueError, TypeError):
        return {}


def proof_review(user_id: uuid.UUID) -> int:
    """How many of this user's first real submissions must each be approved by the user (0 = no such rule)."""
    return int(proof_map().get(str(user_id), 0))


def set_proof_review(user_id: uuid.UUID, count: int) -> None:
    data = proof_map()
    data[str(user_id)] = max(0, int(count))
    _kv().set(PROOF_KEY, json.dumps(data))


def reviewed_so_far(session, user_id: uuid.UUID) -> int:
    """Submissions that went out after the user's own approval."""
    from sqlalchemy import func, select

    from app.models import Application
    from app.services.rules import SENT_STATES

    return int(session.execute(select(func.count()).select_from(Application).where(
        Application.user_id == user_id, Application.approved_at.is_not(None),
        Application.sent_at.is_not(None), Application.status.in_(SENT_STATES))).scalar() or 0)
