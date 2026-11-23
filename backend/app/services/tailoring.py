"""Asking AI CV Studio for a CV tailored to one job (specification section 11, `docs/CV_TAILORING_API.md`).

Called only from this server. Off until `CV_TAILOR_URL` is set. Any failure, quota or doubt means the application
goes out with the person's own CV, so tailoring can never block or endanger an application.
"""

import hashlib
import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, storage
from app.config import get_settings
from app.llm.base import LLMClient
from app.models import Application, AuditLog, CVVersion, Match, Posting, User
from app.services import cvs as cv_service
from app.services.writing import fact_check

log = logging.getLogger(__name__)
MAX_TERMS = 8
_paused_until = 0.0   # after quota_exhausted the service is not asked again until then
PAUSE_SECONDS = 3600
TEMPORARY = {"unavailable", "internal_error", "rate_limited", "quota_exhausted", "timeout", "429", "500", "502", "503", "504"}
VERSION = 1   # part of the idempotency key: raise it to make the service redo earlier requests


class TailorError(Exception):
    """The service could not deliver. `code` is the service's error code when it sent one."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class TailorClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, http: httpx.Client | None = None,
                 sleep=time.sleep):
        s = get_settings()
        self.base = (base_url or s.cv_tailor_url).rstrip("/")
        if not self.base.startswith("https://") and "localhost" not in self.base and "127.0.0.1" not in self.base:
            raise TailorError("The tailoring service address must be https")
        self._headers = {"Authorization": f"Bearer {api_key or s.cv_tailor_api_key}"}
        self._http = http or httpx.Client(timeout=70)
        self._sleep = sleep

    @staticmethod
    def _error(resp: httpx.Response) -> TailorError:
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {}
        return TailorError(err.get("message") or f"HTTP {resp.status_code}", err.get("code") or str(resp.status_code))

    def tailor(self, body: dict, idempotency_key: str) -> dict:
        s = get_settings()
        resp = self._http.post(f"{self.base}/tailor", json=body,
                               headers={**self._headers, "Idempotency-Key": idempotency_key[:100]})
        if resp.status_code not in (200, 202):
            raise self._error(resp)
        data = resp.json()
        waited = 0.0
        while data.get("status") == "processing":
            if waited >= s.cv_tailor_wait_seconds:
                raise TailorError("The service did not finish in time", "timeout")
            self._sleep(s.cv_tailor_poll_seconds)
            waited += s.cv_tailor_poll_seconds
            poll = self._http.get(f"{self.base}/tailor/{data['id']}", headers=self._headers)
            if poll.status_code != 200:
                raise self._error(poll)
            data = poll.json()
        if data.get("status") != "completed":
            err = data.get("error") or {}
            raise TailorError(err.get("message", "The service failed"), err.get("code", "failed"))
        return data

    def pdf(self, result: dict) -> bytes:
        path = "/" + str(result.get("pdf_path", "")).lstrip("/")
        resp = self._http.get(self.base.removesuffix("/v1") + "/v1" + path.removeprefix("/v1"), headers=self._headers)
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
            raise TailorError("The service did not return a PDF", str(resp.status_code))
        return resp.content

    def delete_user(self, user_ref: str) -> int:
        resp = self._http.delete(f"{self.base}/users/{user_ref}", headers=self._headers)
        if resp.status_code != 200:
            raise self._error(resp)
        try:
            return int(resp.json().get("deleted", 0))
        except (ValueError, TypeError, AttributeError):
            return 0


def enabled() -> bool:
    s = get_settings()
    return bool(s.cv_tailor_url and s.cv_tailor_api_key)


def user_ref(user_id: uuid.UUID) -> str:
    """An opaque reference, never an email address or name."""
    return f"u_{user_id.hex}"


def emphasis_for(rubric: dict, cv_text: str) -> list[dict]:
    """Terms the posting treats as essential that the CV proves with a line of its own but words differently:
    these are the ones worth bringing forward. Each carries the CV line that supports it."""
    low = cv_text.lower()
    terms = []
    for r in (rubric or {}).get("requirements", []):
        evidence = (r.get("evidence") or "").strip()
        term = (r.get("requirement") or "").strip()
        if not (r.get("essential") and r.get("status") in ("met", "partial") and evidence and term):
            continue
        if evidence.lower() not in low:
            continue                         # the "evidence" is not really in the CV: never pass it on
        if term.lower() in low:
            continue                         # the CV already says it in the posting's own words
        terms.append({"term": term[:100], "evidence": evidence[:400]})
    return terms[:MAX_TERMS]


def should_tailor(match: Match, emphasis: list[dict]) -> bool:
    return (enabled() and time.time() >= _paused_until and match.decision in ("apply", "manual") and (match.total_score or 0) >= get_settings().cv_tailor_min_score
            and len(emphasis) >= get_settings().cv_tailor_min_terms)


def _norm_term(term: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9#+.\s]", " ", term.lower()).split())


def _covers(have: str, need: str) -> bool:
    """A saved keyword covers a needed one when they are the same or one contains the other ("SQL Server" and
    "Microsoft SQL Server")."""
    return have == need or (len(need) > 2 and (need in have or have in need) and min(len(have), len(need)) > 2)


def library_match(session: Session, user_id: uuid.UUID, terms: set[str], purpose: str) -> dict | None:
    """The person's own library of tailored CVs: the saved one whose keywords cover the most of what this job needs.
    Good enough means at least `cv_tailor_reuse_coverage` of the needed keywords. A CV made for the ATS-safe upload
    layout may also serve an email."""
    needed = {_norm_term(t) for t in terms if _norm_term(t)}
    if not needed:
        return None
    rows = session.execute(select(Application.attachments).where(Application.user_id == user_id)
                           .order_by(Application.created_at.desc()).limit(1000)).scalars()
    best, best_key = None, None
    for attachments in rows:
        for a in attachments or []:
            t = a.get("tailored")
            if not t or (t.get("purpose") != purpose and t.get("purpose") != "upload"):
                continue
            have = {_norm_term(x) for x in t.get("terms", [])}
            covered = sum(1 for n in needed if any(_covers(h, n) for h in have))
            coverage = covered / len(needed)
            if coverage < get_settings().cv_tailor_reuse_coverage:
                continue
            key = (coverage, -len(have - needed))          # most covered first, then the closest (fewest unrelated keywords)
            if best_key is None or key > best_key:
                best, best_key = a, key
    return best


def _asked_enough_today(session: Session, user_id: uuid.UUID) -> bool:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    n = session.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.user_id == user_id, AuditLog.tool == "tailor_cv", AuditLog.created_at >= start)).scalar_one()
    return n >= get_settings().cv_tailor_daily_per_user


def _refused_before(session: Session, user_id: uuid.UUID, family: str, terms: set[str]) -> bool:
    """The service or our fact-check already refused this kind of job with these terms, and asking again changes nothing."""
    since = datetime.now(UTC) - timedelta(days=get_settings().cv_tailor_retry_days)
    rows = session.execute(select(AuditLog.result).where(
        AuditLog.user_id == user_id, AuditLog.tool == "tailor_cv", AuditLog.created_at >= since)).scalars()
    return any(r and r.get("permanent") and r.get("family") == family and terms <= set(r.get("terms", [])) for r in rows)


def tailored_attachment(session: Session, llm: LLMClient, *, user: User, posting: Posting, match: Match, version: CVVersion,
                        purpose: str, client: TailorClient | None = None) -> dict | None:
    """The attachment dict for a tailored CV, or None to use the person's own CV."""
    emphasis = emphasis_for(match.rubric or {}, version.extracted_text or "")
    if not should_tailor(match, emphasis):
        return None
    family = str((posting.fields or {}).get("role_family") or "unknown")
    terms = {e["term"].lower() for e in emphasis}
    hit = library_match(session, user.id, terms, purpose)
    if hit:
        return hit
    if _refused_before(session, user.id, family, terms) or _asked_enough_today(session, user.id):
        return None
    fields = posting.fields or {}
    job_language = (fields.get("language") or "en")[:2].lower()
    if job_language != "en":
        return None                          # the service is English only: it refuses anything else
    country = (posting.country or "").strip()
    body = {
        "user_ref": user_ref(user.id),
        "base_cv": {"profile": version.profile},
        "job": {"title": posting.title, "company": getattr(posting, "company_name", "") or fields.get("company", "") or "",
                "description": (posting.description or "")[:20000], "language": "en",
                **({"country": country.upper()} if len(country) == 2 and country.isalpha() else {})},
        "emphasis": emphasis, "max_pages": 2, "purpose": purpose,
    }
    if purpose == "upload" and version.file_key:
        pass          # the structured profile is enough; the original file is sent only when the profile lacks facts
    # The service answers 409 when a key is reused with a different body, so the body is part of the key.
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    key = f"t{VERSION}-{user.id.hex[:12]}-{posting.id.hex[:12]}-{purpose}-{digest}"
    try:
        client = client or TailorClient()
        result = client.tailor(body, key)
        text = result.get("text", "")
        facts = cv_service.get_fact_sheet(session, user.id)
        unsupported = fact_check(llm, text, version.profile, facts) if text else ["no text returned"]
        if unsupported:
            audit.record(session, user_id=user.id, actor="agent", tool="tailor_cv", result={"used": False, "unsupported": unsupported[:5], "permanent": True, "family": family, "terms": sorted(terms)},
                         reason="the tailored CV stated something the original does not: the original CV is used")
            return None
        pdf = client.pdf(result)
    except (TailorError, httpx.HTTPError) as exc:
        if getattr(exc, "code", "") == "quota_exhausted":
            global _paused_until
            _paused_until = time.time() + PAUSE_SECONDS
        audit.record(session, user_id=user.id, actor="agent", tool="tailor_cv",
                     result={"used": False, "error": getattr(exc, "code", type(exc).__name__), "family": family, "terms": sorted(terms),
                             "permanent": str(getattr(exc, "code", "")) not in TEMPORARY and not isinstance(exc, httpx.HTTPError)},
                     reason=f"tailoring unavailable: {exc}"[:300])
        return None
    name = f"{(version.profile.get('name') or 'CV').replace(' ', '_')}_CV.pdf"
    file_key, _ = storage.save(user.id, name, pdf)
    audit.record(session, user_id=user.id, actor="agent", tool="tailor_cv",
                 result={"used": True, "request_id": result.get("id"), "template": result.get("template"),
                         "changes": len(result.get("changes", [])), "warnings": (result.get("warnings") or [])[:5], "cost_usd": (result.get("usage") or {}).get("cost_usd")},
                 reason="a tailored CV was made for this job")
    return {"name": name, "key": file_key, "tailored": {
        "family": family, "purpose": purpose, "terms": sorted(terms), "request_id": result.get("id"),
        "template": result.get("template"), "changes": result.get("changes", [])[:40]}}


def delete_remote_data(user_id: uuid.UUID, client: TailorClient | None = None) -> None:
    """Ask the service to forget a person who deleted their account. A failure is logged, never blocks deletion."""
    if not enabled():
        return
    try:
        (client or TailorClient()).delete_user(user_ref(user_id))
    except (TailorError, httpx.HTTPError):
        log.warning("could not ask the tailoring service to delete data for %s", user_ref(user_id))
