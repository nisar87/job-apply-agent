"""Which Gemini key a user's work runs on, and how much it may spend a day.

Two ways to run the product, chosen by the admin (key_policy):
- "platform": the operator's key (GEMINI_API_KEY) serves every user, under limits the admin sets.
- "own": every user brings their own key and sets their own limit; nothing runs without one.
- "either": a user's own key when they added one, otherwise the operator's key if the admin allows it for them.
"""

import json
import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.base import LLMClient, SpendCapExceeded
from app.models import User
from app.security import decrypt_secret, encrypt_secret

POLICIES = ("platform", "own", "either")
SETTINGS_KEY = "platform:settings"


class NoModelKey(SpendCapExceeded):
    """No key may be used for this user. A SpendCapExceeded, so every run already stops cleanly on it."""


def _kv():
    from app.tasks.locks import get_redis

    return get_redis()


def platform_settings() -> dict:
    """The admin's choices, with defaults for anything not set yet."""
    s = get_settings()
    stored = {}
    try:
        stored = json.loads(_kv().get(SETTINGS_KEY) or "{}")
    except (ValueError, TypeError):
        stored = {}
    return {
        "key_policy": stored.get("key_policy") if stored.get("key_policy") in POLICIES else "own",
        "default_cap_usd": float(stored.get("default_cap_usd", s.llm_daily_spend_cap_usd)),
        "platform_key_configured": bool(s.gemini_api_key) or s.llm_provider == "fake",
    }


def save_platform_settings(key_policy: str | None = None, default_cap_usd: float | None = None) -> dict:
    current = platform_settings()
    if key_policy is not None:
        if key_policy not in POLICIES:
            raise ValueError(f"key_policy must be one of {', '.join(POLICIES)}")
        current["key_policy"] = key_policy
    if default_cap_usd is not None:
        if not 0 <= default_cap_usd <= 100:
            raise ValueError("the daily cap must be between 0 and 100 dollars")
        current["default_cap_usd"] = round(default_cap_usd, 2)
    _kv().set(SETTINGS_KEY, json.dumps({"key_policy": current["key_policy"], "default_cap_usd": current["default_cap_usd"]}))
    return platform_settings()


def own_key(user: User) -> str:
    if not user.llm_api_key_enc:
        return ""
    try:
        return decrypt_secret(user.llm_api_key_enc)
    except Exception:  # noqa: BLE001 - an unreadable key counts as no key
        return ""


def set_own_key(user: User, key: str) -> None:
    user.llm_api_key_enc = encrypt_secret(key.strip()) if key.strip() else ""


def own_claude_key(user: User) -> str:
    if not user.claude_api_key_enc:
        return ""
    try:
        return decrypt_secret(user.claude_api_key_enc)
    except Exception:  # noqa: BLE001
        return ""


def set_own_claude_key(user: User, key: str) -> None:
    user.claude_api_key_enc = encrypt_secret(key.strip()) if key.strip() else ""


def claude_key_hint(user: User) -> str:
    key = own_claude_key(user)
    return f"…{key[-4:]}" if key else ""


def key_hint(user: User) -> str:
    key = own_key(user)
    return f"…{key[-4:]}" if key else ""


def source_for(user: User, settings: dict | None = None) -> str:
    """'own', 'platform' or 'none': the key this user's work runs on right now."""
    settings = settings or platform_settings()
    policy = settings["key_policy"]
    if policy in ("own", "either") and (own_key(user) or own_claude_key(user)):
        return "own"
    if policy in ("platform", "either") and user.platform_key_allowed and settings["platform_key_configured"]:
        return "platform"
    return "none"


def cap_for(user: User, settings: dict | None = None) -> float:
    """Daily spend cap in dollars; 0 means no cap (only for a user's own key)."""
    settings = settings or platform_settings()
    if source_for(user, settings) == "own":
        return float(user.own_key_cap_usd or 0.0)
    return float(user.platform_cap_usd if user.platform_cap_usd is not None else settings["default_cap_usd"])


def why_no_key(settings: dict) -> str:
    if settings["key_policy"] == "own":
        return "no key: add your Gemini key in Settings (a Claude key too, if you have one)"
    return "no key: add your Gemini key in Settings (a Claude key too, if you have one), or ask the admin for access"


def client_for(session: Session, user_id: uuid.UUID) -> LLMClient:
    """The model client for this user's work. Raises NoModelKey when no key may be used.

    The rule is simple. Both keys given: Claude does the strong tasks, Gemini the quick ones, each covering for the
    other. Only one given: that one does everything. None given: the service's keys, if the admin allows this person.
    Searching and comparing (embeddings, web search) need Gemini, so a Claude-only person uses the service's Gemini for those."""
    from app.llm import _override, get_llm

    if _override is not None:  # tests
        return _override
    user = session.get(User, user_id)
    settings = platform_settings()
    source = source_for(user, settings) if user else "none"
    if source == "none":
        raise NoModelKey(why_no_key(settings))
    if get_settings().llm_provider == "fake":
        return get_llm()
    from app.llm.claude import ClaudeClient
    from app.llm.gemini import GeminiClient
    from app.llm.routing import RoutedLLM

    s = get_settings()
    allowed = bool(user.platform_key_allowed) and settings["key_policy"] != "own"
    if source == "platform":                          # nothing of their own: the service's pair
        return get_llm()
    gemini_key, claude_key = own_key(user), own_claude_key(user)     # what they gave is what runs, nothing else
    gemini = GeminiClient(api_key=gemini_key) if gemini_key else None
    claude = ClaudeClient(api_key=claude_key) if claude_key else None
    if gemini and claude:
        return RoutedLLM(gemini, claude)
    if gemini:
        return gemini
    if claude:
        helper = GeminiClient(api_key=s.gemini_api_key) if (s.gemini_api_key and allowed) else None
        return RoutedLLM(helper, claude)
    raise NoModelKey(why_no_key(settings))


def llm_for(session: Session, user_id: uuid.UUID):
    from app.llm.base import MeteredLLM

    return MeteredLLM(client_for(session, user_id), session, user_id)
