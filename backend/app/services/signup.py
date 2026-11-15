"""Who may create an account: anyone, or only invited addresses. The admin sets this in the admin panel;
addresses in SIGNUP_ALLOWLIST are always invited."""

import json

from app.config import get_settings

KEY = "platform:signup"
MODES = ("invite", "open")


def _kv():
    from app.tasks.locks import get_redis

    return get_redis()


def _env_invites() -> list[str]:
    return sorted({e.strip().lower() for e in get_settings().signup_allowlist.split(",") if e.strip()})


def state() -> dict:
    try:
        stored = json.loads(_kv().get(KEY) or "{}")
    except (ValueError, TypeError):
        stored = {}
    fixed = _env_invites()
    mode = stored.get("mode") if stored.get("mode") in MODES else ("invite" if fixed else "open")
    invites = sorted({e.lower() for e in stored.get("invites", []) if isinstance(e, str)} - set(fixed))
    return {"mode": mode, "invites": invites, "fixed_invites": fixed}


def save(mode: str | None = None, add: list[str] | None = None, remove: list[str] | None = None) -> dict:
    current = state()
    if mode is not None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        current["mode"] = mode
    invites = set(current["invites"]) | {e.strip().lower() for e in add or [] if e.strip()}
    invites -= {e.strip().lower() for e in remove or []}
    _kv().set(KEY, json.dumps({"mode": current["mode"], "invites": sorted(invites)[:1000]}))
    return state()


def may_sign_up(email: str) -> bool:
    s = state()
    email = email.strip().lower()
    return s["mode"] == "open" or email in s["invites"] or email in s["fixed_invites"]
