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
