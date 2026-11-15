"""Applications, drafts, skipped postings, dashboard, notifications, mail and manual runs."""

import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.agent import runs as agent_runs
from app.api.core import _llm, _own
from app.api.deps import current_user_id, db
from app.api.schemas import ApplicationOut, AuditOut, MatchOut, NotificationOut
from app.config import get_settings
from app.connectors import mail as mail_connector
from app.connectors.fetcher import Fetcher, FetchRefused
from app.connectors.sources import RawPosting
from app.db import user_session
from app.llm import SpendCapExceeded, get_llm
from app.models import (
    CV,
    Application,
    AuditLog,
    Campaign,
    Company,
    CVVersion,
    MailAccount,
    Match,
    Message,
    Notification,
    Posting,
    User,
)
from app.security import encrypt_secret
from app.services import applications as app_service
from app.services import postings as posting_service
from app.services import rules
from app.services.campaigns import get_config
from app.tasks.locks import LockBusy, get_redis, run_lock

router = APIRouter(prefix="/api", tags=["work"])


def _app_out(session: Session, app: Application) -> ApplicationOut:
    out = ApplicationOut.model_validate(app)
    posting = session.get(Posting, app.posting_id)
    if posting:
        company = session.get(Company, posting.company_id) if posting.company_id else None
        out.title, out.url = posting.title, posting.url
        out.company = company.name if company else ""
        out.location = (posting.fields or {}).get("location", "") or ""
    if app.cv_version_id:
        version = session.get(CVVersion, app.cv_version_id)
        cv = session.get(CV, version.cv_id) if version else None
        out.cv_label = cv.label if cv else ""
    campaign = session.get(Campaign, app.campaign_id)
    out.campaign_name = campaign.name if campaign else ""
    match = session.get(Match, app.match_id) if app.match_id else None
    if match is not None:
        rubric = match.rubric or {}
        out.match_summary = str(rubric.get("summary") or "")[:600]
        out.match_strengths = [str(r.get("requirement")) for r in rubric.get("requirements", [])
                               if r.get("status") == "met" and r.get("requirement")][:5]
    return out


# ---- Applications ----

@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(
    status: str | None = None, campaign_id: uuid.UUID | None = None, q: str | None = None,
    since: datetime | None = None, until: datetime | None = None, limit: int = Query(200, le=500),
    user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function"),
):
    query = select(Application).where(Application.user_id == user_id)
    if status:
        query = query.where(Application.status.in_(status.split(",")))
    if campaign_id:
        query = query.where(Application.campaign_id == campaign_id)
    if since:
        query = query.where(Application.created_at >= since)
    if until:
        query = query.where(Application.created_at <= until)
    rows = session.execute(query.order_by(func.coalesce(Application.sent_at, Application.created_at).desc(), Application.score.desc().nulls_last(),
                                         Application.id).limit(limit)).scalars()
    out = [_app_out(session, a) for a in rows]
    if q:
        needle = q.lower()
        out = [o for o in out if needle in f"{o.title} {o.company}".lower()]
    return out


@router.get("/applications/{app_id}")
def application_detail(app_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    app = _own(session, Application, app_id)
    posting = session.get(Posting, app.posting_id)
    match = session.get(Match, app.match_id) if app.match_id else None
    messages = session.execute(select(Message).where(Message.application_id == app.id).order_by(Message.received_at)).scalars()
    timeline = session.execute(
        select(AuditLog).where(AuditLog.entity_id.in_([i for i in (app.id, app.match_id) if i])).order_by(AuditLog.created_at)
    ).scalars()
    return {
        "application": _app_out(session, app),
        "posting": {"title": posting.title, "url": posting.url, "description": posting.description, "fields": posting.fields,
                    "posted_date": posting.posted_date, "first_seen": posting.first_seen},
        "match": {"total_score": match.total_score, "rubric": match.rubric, "reason": match.reason,
                  "filter_result": match.filter_result} if match else None,
        "cv_version_id": app.cv_version_id,
        "messages": [{"id": m.id, "direction": m.direction, "sender": m.sender, "subject": m.subject, "body": m.body,
                      "received_at": m.received_at, "category": m.category, "summary": m.summary,
                      "handled_by": m.handled_by} for m in messages],
        "timeline": [AuditOut.model_validate(t) for t in timeline],
    }


class ApproveIn(BaseModel):
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=20000)


class RejectIn(BaseModel):
    reason: str = Field(default="", max_length=500)
    never_company: bool = False


@router.post("/applications/{app_id}/approve", response_model=ApplicationOut)
def approve(app_id: uuid.UUID, body: ApproveIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    app = _own(session, Application, app_id)
    try:
        app_service.approve(session, app, subject=body.subject, body=body.body, llm=_llm(session, user_id))
    except app_service.ApplicationError as exc:
        session.commit()  # keep the edit and the recorded problems
        raise HTTPException(409, str(exc)) from exc
    return _app_out(session, app)


@router.post("/applications/{app_id}/reject", response_model=ApplicationOut)
def reject(app_id: uuid.UUID, body: RejectIn, session: Session = Depends(db, scope="function")):
    app = _own(session, Application, app_id)
    try:
        app_service.reject(session, app, body.reason, body.never_company)
    except app_service.ApplicationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _app_out(session, app)


# ---- Skipped postings and "apply anyway" ----

@router.get("/matches", response_model=list[MatchOut])
def list_matches(decision: str = "skip", limit: int = Query(200, le=500),
                 user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    rows = session.execute(
        select(Match).where(Match.user_id == user_id, Match.decision.in_(decision.split(",")))
        .order_by(Match.created_at.desc()).limit(limit)
    ).scalars()
    out = []
    for m in rows:
        o = MatchOut.model_validate(m)
        posting = session.get(Posting, m.posting_id)
        company = session.get(Company, posting.company_id) if posting and posting.company_id else None
        o.title, o.url, o.route = posting.title, posting.url, posting.apply_route
        o.company = company.name if company else ""
        out.append(o)
    return out


@router.post("/matches/{match_id}/apply-anyway", response_model=ApplicationOut)
def apply_anyway(match_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    match = _own(session, Match, match_id)
    try:
        app = app_service.override_and_draft(session, _llm(session, user_id), match)
    except app_service.ApplicationError as exc:
        raise HTTPException(409, str(exc)) from exc
    except SpendCapExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    return _app_out(session, app)


# ---- Add a job by link ----

class AddJobIn(BaseModel):
    url: str = Field(max_length=2000)
    campaign_id: uuid.UUID


@router.post("/postings/by-url")
def add_by_url(body: AddJobIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    campaign = _own(session, Campaign, body.campaign_id)
    try:
        text = Fetcher().fetch_text(body.url)
    except FetchRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(400, "The page could not be fetched") from exc
    title = text.strip().split("\n", 1)[0][:200] or "Job posting"
    llm = _llm(session, user_id)
    raw = RawPosting(source="user_link", source_id=body.url, url=body.url, title=title, company="", description=text)
    posting, _ = posting_service.ingest(session, llm, raw)
    from app.agent.tools import AgentContext, draft_application, score_job

    ctx = AgentContext(session=session, llm=llm, campaign=campaign, user=session.get(User, user_id))
    verdict = score_job(ctx, str(posting.id))
    drafted = draft_application(ctx, str(posting.id)) if verdict.get("decision") in ("apply", "manual") else None
    return {"posting_id": posting.id, "verdict": verdict, "draft": drafted}


# ---- Dashboard ----

@router.get("/dashboard")
def dashboard(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    user = session.get(User, user_id)
    zone = rules.user_zone(user)
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)

    def count(*conds) -> int:
        return int(session.execute(select(func.count()).select_from(Application).where(Application.user_id == user_id, *conds)).scalar() or 0)

    campaigns = session.execute(select(Campaign).where(Campaign.user_id == user_id)).scalars().all()
    quota = sum(get_config(c).daily_quota for c in campaigns if c.state != "paused")
    matches_week = int(session.execute(
        select(func.count()).select_from(Match).where(Match.user_id == user_id, Match.created_at >= week_ago)).scalar() or 0)
    matched_week = int(session.execute(
        select(func.count()).select_from(Match).where(Match.user_id == user_id, Match.created_at >= week_ago,
                                                      Match.decision.in_(("apply", "manual")))).scalar() or 0)
    return {
        "today": {
            "sent": rules.sent_today(session, user_id, zone, now), "quota": min(quota, user.daily_send_limit),
            "drafts_waiting": count(Application.status == "awaiting_approval"),
            "queued": count(Application.status == "queued"),
            "needs_attention": count(Application.status == "failed"),
            "manual_apply": count(Application.status == "manual_apply"),
        },
        "week": {
            "evaluated": matches_week, "matched": matched_week,
            "applied": count(Application.sent_at >= week_ago),
            "replied": count(Application.sent_at >= week_ago, Application.status.in_(("in_conversation", "interview", "offer", "rejected", "closed", "acknowledged"))),
            "interviews": count(Application.status == "interview", Application.updated_at >= week_ago),
            "rejections": count(Application.status == "rejected", Application.updated_at >= week_ago),
        },
        "campaigns": [{"id": c.id, "name": c.name, "state": c.state} for c in campaigns],
    }


# ---- Notifications ----

@router.get("/notifications", response_model=list[NotificationOut])
def notifications(unread_only: bool = False, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    q = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        q = q.where(Notification.read.is_(False))
    return list(session.execute(q.order_by(Notification.created_at.desc()).limit(100)).scalars())


@router.post("/notifications/{note_id}/read", status_code=204)
def mark_read(note_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    _own(session, Notification, note_id).read = True


# ---- Manual runs ----

@router.post("/campaigns/{campaign_id}/run")
def run_now(campaign_id: uuid.UUID, kind: str = "prepare", user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    """Start a run now instead of waiting for the schedule."""
    campaign = _own(session, Campaign, campaign_id)
    if kind not in ("discovery", "prepare", "dispatch"):
        raise HTTPException(422, "kind must be discovery, prepare or dispatch")
    if get_settings().manual_runs_in_background:
        # Behind a proxy with a short request limit (Firebase Hosting: 60 seconds) a long run would be cut
        # off, so it runs after the response instead. The run itself is the same, with the same lock.
        if get_redis().get(f"lock:{kind}:{user_id}"):
            raise HTTPException(409, "A run of this kind is already in progress")
        threading.Thread(target=_run_in_background, args=(user_id, campaign_id, kind), daemon=True).start()
        return {"started": True, "kind": kind}
    try:
        with run_lock(user_id, kind):
            if kind == "discovery":
                return agent_runs.discovery_run(session, _llm(session, user_id).client, campaign, fetcher=Fetcher())
            if kind == "prepare":
                return agent_runs.prepare_run(session, _llm(session, user_id).client, campaign)
            return agent_runs.dispatch_run(session, user_id)
    except LockBusy as exc:
        raise HTTPException(409, "A run of this kind is already in progress") from exc


def _run_in_background(user_id: uuid.UUID, campaign_id: uuid.UUID, kind: str) -> None:
    """A manual run in its own session; failures are recorded on the run like scheduled ones."""
    try:
        with run_lock(user_id, kind), user_session(user_id) as session:
            campaign = session.get(Campaign, campaign_id)
            llm = get_llm()
            if kind == "discovery":
                agent_runs.discovery_run(session, llm, campaign, fetcher=Fetcher())
            elif kind == "prepare":
                agent_runs.prepare_run(session, llm, campaign)
            else:
                agent_runs.dispatch_run(session, user_id)
    except LockBusy:
        pass
    except Exception:  # noqa: BLE001 - logged; the run row already says it failed
        logging.getLogger(__name__).exception("background %s run failed for %s", kind, campaign_id)


# ---- Mail connection ----

def _state_token(user_id: uuid.UUID) -> str:
    return jwt.encode({"sub": str(user_id), "exp": datetime.now(UTC) + timedelta(minutes=15), "aud": "mail-connect"},
                      get_settings().secret_key, algorithm="HS256")


@router.get("/mail/account")
def mail_status(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    rows = session.execute(select(MailAccount).where(MailAccount.user_id == user_id)).scalars()
    return [{"id": a.id, "address": a.address, "provider": a.provider, "status": a.status, "watch_expiry": a.watch_expiry} for a in rows]


@router.get("/mail/google/start")
def google_start(user_id: uuid.UUID = Depends(current_user_id)):
    s = get_settings()
    if s.mail_provider == "fake":
        # Practice mode: connect the in-memory mailbox under the account's own address.
        with user_session(user_id) as session:
            _store_account(session, user_id, session.get(User, user_id).email, "fake", "fake-token", None)
        return {"url": f"{s.frontend_url}/account?mail=connected"}
    if not s.google_client_id:
        raise HTTPException(503, "Google sign-in for Gmail is not configured on this server")
    params = {
        "client_id": s.google_client_id, "redirect_uri": s.google_redirect_uri, "response_type": "code",
        "scope": " ".join(mail_connector.GMAIL_SCOPES), "access_type": "offline", "prompt": "consent",
        "state": _state_token(user_id),
    }
    return {"url": f"{mail_connector.GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/mail/google/callback")
def google_callback(code: str = "", state: str = "", error: str = ""):
    s = get_settings()
    target = f"{s.frontend_url}/account"
    if error or not code:
        return RedirectResponse(f"{target}?mail=declined")
    try:
        user_id = uuid.UUID(jwt.decode(state, s.secret_key, algorithms=["HS256"], audience="mail-connect")["sub"])
    except Exception:  # noqa: BLE001
        return RedirectResponse(f"{target}?mail=expired")
    http = httpx.Client(timeout=30)
    tok = http.post(mail_connector.GOOGLE_TOKEN_URL, data={
        "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
        "redirect_uri": s.google_redirect_uri, "grant_type": "authorization_code",
    })
    if tok.status_code != 200 or "refresh_token" not in tok.json():
        return RedirectResponse(f"{target}?mail=failed")
    data = tok.json()
    profile = http.get(f"{mail_connector.GMAIL_API}/profile", headers={"Authorization": f"Bearer {data['access_token']}"})
    if profile.status_code != 200:
        return RedirectResponse(f"{target}?mail=failed")
    address = profile.json()["emailAddress"].lower()
    with user_session(user_id) as session:
        _store_account(session, user_id, address, "gmail", data["refresh_token"], str(profile.json().get("historyId", "")))
    return RedirectResponse(f"{target}?mail=connected")


def _store_account(session: Session, user_id: uuid.UUID, address: str, provider: str, refresh_token: str, history_id: str | None) -> MailAccount:
    account = session.execute(select(MailAccount).where(MailAccount.user_id == user_id, MailAccount.address == address)).scalar_one_or_none()
    if account is None:
        account = MailAccount(user_id=user_id, address=address, provider=provider)
        session.add(account)
    account.encrypted_refresh_token = encrypt_secret(refresh_token)
    account.scopes = mail_connector.GMAIL_SCOPES
    account.status = "connected"
    account.last_history_id = history_id or None
    session.flush()
    try:
        account.watch_expiry, hist = mail_connector.get_mail_provider().watch(account)
        account.last_history_id = account.last_history_id or hist
    except Exception:  # noqa: BLE001 - the safety poll covers a failed watch
        pass
    audit.record(session, user_id=user_id, actor="user", tool="connect_mail", input={"address": address})
    app_service.resume_after_reconnect(session, user_id)
    return account


class FakeConnectIn(BaseModel):
    address: EmailStr


@router.post("/mail/fake/connect")
def fake_connect(body: FakeConnectIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    """Development only: connect the in-memory mailbox so the whole flow can be tried without Google."""
    if get_settings().mail_provider != "fake":
        raise HTTPException(404, "Not found")
    account = _store_account(session, user_id, body.address.lower(), "fake", "fake-token", None)
    return {"id": account.id, "address": account.address}


@router.delete("/mail/account/{account_id}", status_code=204)
def disconnect_mail(account_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    account = _own(session, MailAccount, account_id)
    if account.provider == "gmail":
        try:  # revoke at Google, then forget the token
            from app.security import decrypt_secret

            httpx.post("https://oauth2.googleapis.com/revoke", params={"token": decrypt_secret(account.encrypted_refresh_token)}, timeout=15)
        except Exception:  # noqa: BLE001
            pass
    session.delete(account)
    audit.record(session, user_id=user_id, actor="user", tool="disconnect_mail", input={"address": account.address})


# ---- Jobs to apply to on the employer's site ----

@router.post("/applications/{app_id}/applied", response_model=ApplicationOut)
def applied_by_hand(app_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    """The user applied on the employer's site; from now on it counts as sent."""
    app = _own(session, Application, app_id)
    try:
        app_service.mark_applied_by_hand(session, app)
    except app_service.ApplicationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _app_out(session, app)


@router.post("/applications/{app_id}/skip", response_model=ApplicationOut)
def skip_on_site(app_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    app = _own(session, Application, app_id)
    try:
        app_service.skip_manual(session, app, "you passed on it")
    except app_service.ApplicationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _app_out(session, app)


# ---- Web application forms (phase 3) ----

@router.post("/applications/{app_id}/form/prepare", response_model=ApplicationOut)
def form_prepare(app_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    """Fill the employer's form without submitting it, and keep a screenshot for review."""
    from app.services import forms

    app = _own(session, Application, app_id)
    try:
        forms.prepare_form(session, _llm(session, user_id), app)
    except forms.FormStopped as stop:
        session.commit()
        raise HTTPException(409, stop.reason) from stop
    return _app_out(session, app)


@router.get("/applications/{app_id}/form")
def form_review(app_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    app = _own(session, Application, app_id)
    data = app.form_data or {}
    fields = {f["id"]: f for f in data.get("fields", [])}
    return {
        "hiring_system": data.get("hiring_system"), "consents": data.get("consents", []),
        "answers": [{"label": fields[k]["label"], "value": v} for k, v in data.get("answers", {}).items() if k in fields],
        "files": [{"label": fields[k]["label"], "file": "your CV" if v == "cv" else "cover letter"} for k, v in data.get("files", {}).items() if k in fields],
        "has_screenshot": bool(data.get("screenshot_key")), "submitted_at": data.get("submitted_at"),
    }


@router.get("/applications/{app_id}/form/screenshot")
def form_screenshot(app_id: uuid.UUID, proof: bool = False, session: Session = Depends(db, scope="function")):
    from fastapi.responses import Response

    from app import storage

    app = _own(session, Application, app_id)
    key = (app.form_data or {}).get("proof_key" if proof else "screenshot_key")
    if not key:
        raise HTTPException(404, "Not found")
    return Response(storage.load(key), media_type="image/png")


@router.post("/applications/{app_id}/form/approve", response_model=ApplicationOut)
def form_approve(app_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    """Approve the filled form. It is submitted now if the rules allow, otherwise in the next send window."""
    from app.services import forms

    app = _own(session, Application, app_id)
    try:
        forms.approve_form(session, app)
        forms.submit_form(session, app)
    except rules.RuleViolation as v:
        if not v.retry_later:
            raise HTTPException(409, v.message) from v
        session.commit()        # approved and waiting for the window, quota or a free place
    except forms.FormStopped as stop:
        session.commit()
        raise HTTPException(409, stop.reason) from stop
    return _app_out(session, app)
