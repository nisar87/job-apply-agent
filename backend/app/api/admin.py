"""Model keys and the admin panel.

Users: add, check or remove their own Gemini key and set its daily limit.
Admin: choose how keys work for everyone, set each user's access and limits, and read the log of
web forms the agent could not fill."""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user_id, db
from app.api.replies import _operator
from app.config import get_settings
from app.db import system_session
from app.llm import access
from app.models import Application, AuditLog, Company, Posting, UsageDaily, User
from app.services import live

router = APIRouter(prefix="/api", tags=["admin"])


# ---- The user's own key ----

def _spent_today(session: Session, user_id: uuid.UUID) -> float:
    today = datetime.now(UTC).date()
    row = session.execute(select(UsageDaily.cost_usd).where(UsageDaily.user_id == user_id, UsageDaily.day == today)).scalar()
    return round(float(row or 0.0), 4)


def key_status(session: Session, user: User) -> dict:
    settings = access.platform_settings()
    source = access.source_for(user, settings)
    return {
        "policy": settings["key_policy"],
        "source": source,                       # own | platform | none
        "hint": access.key_hint(user),          # last four characters of the user's own key
        "has_own_key": bool(access.own_key(user)),
        "claude_hint": access.claude_key_hint(user),
        "has_claude_key": bool(access.own_claude_key(user)),
        "own_key_cap_usd": user.own_key_cap_usd,
        "platform_key_allowed": user.platform_key_allowed and settings["key_policy"] != "own",
        "cap_usd": access.cap_for(user, settings),
        "spent_today_usd": _spent_today(session, user.id),
        "message": access.why_no_key(settings) if source == "none" else "",
    }


class KeyIn(BaseModel):
    key: str | None = Field(default=None, max_length=200)
    claude_key: str | None = Field(default=None, max_length=300)
    own_key_cap_usd: float | None = Field(default=None, ge=0, le=100)
    clear_cap: bool = False


def _check_key(key: str) -> None:
    """One tiny request, on the user's own key, to make sure Google accepts it."""
    if not re.fullmatch(r"[A-Za-z0-9_\-.]{20,200}", key):
        raise HTTPException(400, "That does not look like a Gemini API key")
    if get_settings().llm_provider == "fake":
        return
    from app.llm.gemini import GeminiClient

    try:
        GeminiClient(api_key=key).generate("Reply with the single word: ok", task="key_check")
    except Exception as exc:  # noqa: BLE001 - the message from Google is not shown: it can echo the key
        raise HTTPException(400, "Google did not accept this key. Check it in Google AI Studio and try again.") from exc


def _check_claude_key(key: str) -> None:
    """One tiny request on the person's own Claude key."""
    if not re.fullmatch(r"[A-Za-z0-9_\-.]{20,300}", key):
        raise HTTPException(400, "That does not look like a Claude API key")
    if get_settings().llm_provider == "fake":
        return
    from app.llm.claude import ClaudeClient

    try:
        ClaudeClient(api_key=key).generate("Reply with the single word: ok", tier="fast", task="key_check")
    except Exception as exc:  # noqa: BLE001 - the provider's message can echo the key, so it is not shown
        raise HTTPException(400, "Claude did not accept this key. Check the key and your credit at console.anthropic.com.") from exc


@router.get("/account/model-key")
def get_model_key(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    return key_status(session, session.get(User, user_id))


@router.put("/account/model-key")
def put_model_key(body: KeyIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    user = session.get(User, user_id)
    if body.key is not None and body.key.strip():
        key = body.key.strip()
        _check_key(key)
        access.set_own_key(user, key)
        audit.record(session, user_id=user_id, actor="user", tool="set_model_key", result={"hint": f"…{key[-4:]}"})
    if body.claude_key is not None and body.claude_key.strip():
        ckey = body.claude_key.strip()
        _check_claude_key(ckey)
        access.set_own_claude_key(user, ckey)
        audit.record(session, user_id=user_id, actor="user", tool="set_claude_key", result={"hint": f"…{ckey[-4:]}"})
    if body.clear_cap:
        user.own_key_cap_usd = None
    elif body.own_key_cap_usd is not None:
        user.own_key_cap_usd = round(body.own_key_cap_usd, 2)
    session.flush()
    return key_status(session, user)


@router.delete("/account/model-key")
def delete_model_key(which: str = "gemini", user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    user = session.get(User, user_id)
    if which == "claude":
        access.set_own_claude_key(user, "")
    else:
        access.set_own_key(user, "")
    audit.record(session, user_id=user_id, actor="user", tool="remove_model_key", result={"which": which})
    session.flush()
    return key_status(session, user)


# ---- Admin: how keys work for everyone ----

class PlatformIn(BaseModel):
    key_policy: str | None = None
    default_cap_usd: float | None = Field(default=None, ge=0, le=100)


@router.get("/operator/settings")
def get_platform(_: uuid.UUID = Depends(_operator)):
    return access.platform_settings()


@router.put("/operator/settings")
def put_platform(body: PlatformIn, _: uuid.UUID = Depends(_operator)):
    try:
        return access.save_platform_settings(body.key_policy, body.default_cap_usd)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class UserLimitsIn(BaseModel):
    platform_key_allowed: bool | None = None
    platform_cap_usd: float | None = Field(default=None, ge=0, le=100)
    use_default_cap: bool = False
    daily_send_limit: int | None = Field(default=None, ge=0, le=100)
    is_operator: bool | None = None
    real_sending: bool | None = None  # test site only: this user's applications reach employers
    proof_review: int | None = Field(default=None, ge=0, le=500)  # the first N real submissions need the user's approval


@router.patch("/operator/users/{target}")
def set_user_limits(target: uuid.UUID, body: UserLimitsIn, me: uuid.UUID = Depends(_operator)):
    with system_session() as s:
        user = s.get(User, target)
        if not user:
            raise HTTPException(404, "Not found")
        if body.platform_key_allowed is not None:
            user.platform_key_allowed = body.platform_key_allowed
        if body.use_default_cap:
            user.platform_cap_usd = None
        elif body.platform_cap_usd is not None:
            user.platform_cap_usd = round(body.platform_cap_usd, 2)
        if body.daily_send_limit is not None:
            user.daily_send_limit = body.daily_send_limit
        if body.is_operator is not None:
            if target == me and not body.is_operator:
                raise HTTPException(422, "You cannot remove your own admin access")
            user.is_operator = body.is_operator
        if body.real_sending is not None:
            live.set_real(user.id, body.real_sending)
            if body.real_sending and body.proof_review is None and not live.proof_review(user.id):
                live.set_proof_review(user.id, live.PROOF_DEFAULT)      # real sending starts with 30 reviewed submissions
            audit.record(s, user_id=user.id, actor="operator", tool="real_sending",
                         input={"on": body.real_sending, "by": str(me)})
        if body.proof_review is not None:
            live.set_proof_review(user.id, body.proof_review)
            audit.record(s, user_id=user.id, actor="operator", tool="proof_review",
                         input={"count": body.proof_review, "by": str(me)})
        s.flush()
        return _user_row(s, user, access.platform_settings(), {})


class SignupIn(BaseModel):
    mode: str | None = None
    add: list[str] = Field(default_factory=list, max_length=200)
    remove: list[str] = Field(default_factory=list, max_length=200)


@router.post("/operator/tailor-test")
def tailor_test(me: uuid.UUID = Depends(_operator)):
    """One real call to the tailoring service with the operator's own CV and a sample job. Nothing is saved or sent.
    Says whether the address, the key, the service and the PDF download all work."""
    import time

    from app.models import CV, CVVersion
    from app.services import tailoring

    if not tailoring.enabled():
        return {"ok": False, "step": "setup", "why": "CV_TAILOR_URL or CV_TAILOR_API_KEY is not set on this server"}
    with system_session() as s:
        cv = s.execute(select(CV).where(CV.user_id == me, CV.active.is_(True)).order_by(CV.is_default.desc())).scalars().first()
        version = s.get(CVVersion, cv.current_version_id) if cv and cv.current_version_id else None
        if version is None:
            return {"ok": False, "step": "cv", "why": "no CV on this account"}
        profile = version.profile
    body = {
        "user_ref": f"test-{me.hex[:12]}", "base_cv": {"profile": profile},
        "job": {"title": "Senior .NET Developer", "company": "Test Company", "language": "en",
                "description": "We build backend services in C#, .NET and .NET Core with SQL Server and REST APIs. "
                               "Senior engineer, remote, five or more years of experience."},
        "emphasis": [], "max_pages": 2, "purpose": "email",
    }
    started = time.time()
    try:
        client = tailoring.TailorClient()
        result = client.tailor(body, f"test-{me.hex[:12]}-{int(started)}")
        pdf = client.pdf(result)
    except Exception as exc:  # noqa: BLE001 - the point is to report exactly what failed
        out = {"ok": False, "step": "service", "why": f"{type(exc).__name__}: {exc}"[:400],
               "code": getattr(exc, "code", ""), "seconds": round(time.time() - started, 1)}
        logging.getLogger(__name__).warning("tailor-test result: %s", out)   # the page may time out first; the log keeps it
        return out
    out = {"ok": True, "seconds": round(time.time() - started, 1), "pdf_bytes": len(pdf),
           "text_chars": len(result.get("text", "")), "status": result.get("status")}
    logging.getLogger(__name__).warning("tailor-test result: %s", out)
    return out


@router.post("/operator/rewrite-unsent")
def rewrite_unsent(me: uuid.UUID = Depends(_operator)):
    """Write every unsent application email again under the current email rules."""
    from app.llm.access import llm_for
    from app.services import applications as app_service

    with system_session() as s:
        ids = list(s.execute(select(User.id)).scalars().all())
    total = 0
    for uid in ids:
        try:
            with system_session() as s:
                total += app_service.rewrite_unsent(s, llm_for(s, uid), uid)
        except Exception:  # noqa: BLE001 - one person's missing key must not stop the others
            continue
    return {"rewritten": total}


@router.get("/operator/signup")
def get_signup(_: uuid.UUID = Depends(_operator)):
    from app.services import signup

    return signup.state()


@router.put("/operator/signup")
def put_signup(body: SignupIn, me: uuid.UUID = Depends(_operator)):
    from app.services import signup

    bad = [e for e in body.add if not re.fullmatch(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+", e.strip())]
    if bad:
        raise HTTPException(422, f"Not an email address: {', '.join(bad[:3])}")
    try:
        result = signup.save(mode=body.mode, add=body.add, remove=body.remove)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with system_session() as s:
        audit.record(s, user_id=me, actor="operator", tool="signup_settings",
                     input={"mode": body.mode, "add": body.add, "remove": body.remove})
    return result


@router.post("/operator/users/{target}/reset-link")
def make_reset_link(target: uuid.UUID, me: uuid.UUID = Depends(_operator)):
    """For a user who cannot receive the reset email: a one-time link (valid an hour) to pass on to them."""
    from app.services.account import reset_link

    with system_session() as s:
        user = s.get(User, target)
        if not user or user.status == "deleted":
            raise HTTPException(404, "Not found")
        audit.record(s, user_id=user.id, actor="operator", tool="password_reset_link", input={"by": str(me)})
        return {"link": reset_link(user)}


def _user_row(s: Session, u: User, settings: dict, spend: dict) -> dict:
    return {
        "id": u.id, "email": u.email, "status": u.status,
        "key_source": access.source_for(u, settings), "has_own_key": bool(u.llm_api_key_enc),
        "platform_key_allowed": u.platform_key_allowed, "platform_cap_usd": u.platform_cap_usd,
        "cap_usd": access.cap_for(u, settings), "daily_send_limit": u.daily_send_limit,
        "is_admin": u.is_operator or get_settings().is_admin_email(u.email),
        "spend_today_usd": round(float(spend.get(u.id, 0.0)), 4),
        "real_sending": live.sends_for_real(u.id), "production": get_settings().is_production,
        "proof_review": live.proof_review(u.id), "proof_reviewed": live.reviewed_so_far(s, u.id),
    }


@router.get("/operator/users")
def list_users(_: uuid.UUID = Depends(_operator)):
    today = datetime.now(UTC).date()
    settings = access.platform_settings()
    with system_session() as s:
        spend = dict(s.execute(select(UsageDaily.user_id, UsageDaily.cost_usd).where(UsageDaily.day == today)).all())
        users = s.execute(select(User).where(User.status != "deleted").order_by(User.created_at)).scalars().all()
        return [_user_row(s, u, settings, spend) for u in users]


# ---- Admin: web forms the agent could not fill ----

REASON_KINDS = [
    ("captcha", "CAPTCHA"), ("password", "Login needed"), ("payment", "Payment step"),
    ("no application form", "No form on the page"), ("no form fields", "No form on the page"),
    ("fact sheet", "Missing from the fact sheet"), ("changed since", "Form changed"),
    ("timeout", "Page too slow"), ("unexpected error", "Agent error"),
]


def reason_kind(reason: str) -> str:
    low = (reason or "").lower()
    return next((label for needle, label in REASON_KINDS if needle in low), "Other")


@router.get("/operator/form-attempts")
def form_attempts(days: int = Query(14, ge=1, le=90), _: uuid.UUID = Depends(_operator)):
    since = datetime.now(UTC) - timedelta(days=days)
    with system_session() as s:
        rows = s.execute(
            select(AuditLog, User.email).join(User, User.id == AuditLog.user_id)
            .where(AuditLog.tool.in_(("prepare_form", "submit_form")), AuditLog.created_at >= since)
            .order_by(AuditLog.created_at.desc()).limit(500)
        ).all()
        app_ids = {r.AuditLog.entity_id for r in rows if r.AuditLog.entity_id}
        titles = {}
        if app_ids:
            for app_id, title, company_name in s.execute(
                select(Application.id, Posting.title, Company.name).join(Posting, Posting.id == Application.posting_id)
                .outerjoin(Company, Company.id == Posting.company_id).where(Application.id.in_(app_ids))
            ).all():
                titles[app_id] = f"{title} · {company_name}" if company_name else title
        attempts, kinds = [], {}
        filled = submitted = 0
        for row, email in rows:
            res = row.result or {}
            if row.tool == "submit_form":
                submitted += 1 if res.get("submitted") else 0
                if res.get("submitted"):
                    continue
            elif not res.get("stopped"):
                filled += 1
                continue
            kind = reason_kind(row.reason)
            kinds[kind] = kinds.get(kind, 0) + 1
            attempts.append({
                "at": row.created_at, "user": email, "job": titles.get(row.entity_id, ""), "application_id": row.entity_id,
                "url": (row.input or {}).get("url", ""), "hiring_system": (row.input or {}).get("hiring_system", ""),
                "step": "submit" if row.tool == "submit_form" else "fill", "automatic": bool((row.input or {}).get("auto")),
                "attempt": res.get("attempt"), "final": res.get("final"), "kind": kind, "reason": row.reason,
            })
        return {"days": days, "filled": filled, "submitted": submitted, "failed": len(attempts),
                "by_kind": dict(sorted(kinds.items(), key=lambda kv: -kv[1])), "attempts": attempts}
