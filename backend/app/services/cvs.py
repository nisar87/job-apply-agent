"""CV intake, structured profiles and the fact sheet (master document, section 8)."""

import io
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, storage
from app.llm.base import LLMClient
from app.models import CV, CVVersion, FactSheet

MAX_CVS = 4
MAX_BYTES = 5 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".txt"}

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "location": {"type": "string"},
        "summary": {"type": "string"},
        "skills": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "years": {"type": "number"}},
                      "required": ["name"]},
        },
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"}, "company": {"type": "string"}, "start": {"type": "string"},
                    "end": {"type": "string"}, "achievements": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "company"],
            },
        },
        "education": {"type": "array", "items": {"type": "string"}},
        "languages": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "level": {"type": "string"}},
                      "required": ["name"]},
        },
        "certifications": {"type": "array", "items": {"type": "string"}},
        "links": {"type": "array", "items": {"type": "string"}},
        "total_years_experience": {"type": "number"},
    },
    "required": ["name", "summary", "skills", "roles"],
}

FACT_SHEET_FIELDS = [
    "work_authorisation",  # {country code: "citizen" | "permit" | "needs_sponsorship"}
    "notice_period", "earliest_start", "salary_expectation", "salary_currency",
    "willing_to_relocate", "willing_to_travel", "languages", "phone", "interview_availability", "location",
]


class CVError(ValueError):
    pass


def extract_text(name: str, data: bytes) -> str:
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix not in ALLOWED:
        raise CVError("Upload a PDF, DOCX or TXT file")
    if len(data) > MAX_BYTES:
        raise CVError("The file is larger than 5 MB")
    if suffix == ".txt":
        return data.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        if not data.startswith(b"%PDF"):
            raise CVError("This file is not a valid PDF")
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise CVError("This file is not a valid DOCX") from exc
    return "\n".join(p.text for p in doc.paragraphs)


def profile_text(profile: dict) -> str:
    """Flat text of a profile, used for embeddings and prompts."""
    parts = [profile.get("summary", "")]
    parts += [f"{s.get('name', '')} {s.get('years', '')}" for s in profile.get("skills", [])]
    for role in profile.get("roles", []):
        parts.append(f"{role.get('title', '')} at {role.get('company', '')}")
        parts += role.get("achievements", [])
    parts += [f"{lang.get('name', '')} {lang.get('level', '')}" for lang in profile.get("languages", [])]
    parts += profile.get("education", []) + profile.get("certifications", [])
    return "\n".join(p for p in parts if p)


def parse_profile(llm: LLMClient, text: str) -> dict:
    prompt = (
        "Convert this CV into the structured profile. Use only what the CV states; "
        "leave a field empty when the CV does not say.\n\nCV:\n" + text[:30000]
    )
    return llm.generate_structured(prompt, PROFILE_SCHEMA, task="cv_parse").data or {}


def list_cvs(session: Session, user_id: uuid.UUID, active_only: bool = False) -> list[CV]:
    q = select(CV).where(CV.user_id == user_id).order_by(CV.created_at)
    if active_only:
        q = q.where(CV.active.is_(True))
    return list(session.execute(q).scalars())


def current_version(session: Session, cv: CV) -> CVVersion | None:
    return session.get(CVVersion, cv.current_version_id) if cv.current_version_id else None


def _set_default(session: Session, user_id: uuid.UUID, cv: CV) -> None:
    for other in list_cvs(session, user_id):
        other.is_default = other.id == cv.id


def upload_cv(
    session: Session, llm: LLMClient, user_id: uuid.UUID, *, file_name: str, data: bytes, label: str,
    target_roles: list[str] | None = None, keywords: list[str] | None = None, language: str = "en",
    cv_id: uuid.UUID | None = None,
) -> CV:
    """Create a CV, or add a new version to an existing one."""
    text = extract_text(file_name, data)
    if len(text.strip()) < 50:
        raise CVError("No readable text found in this file. If it is a scan, upload a text-based version.")
    if cv_id:
        cv = session.get(CV, cv_id)
        if cv is None:
            raise CVError("CV not found")
    else:
        if len(list_cvs(session, user_id, active_only=True)) >= MAX_CVS:
            raise CVError(f"You can keep up to {MAX_CVS} CVs. Remove one first.")
        cv = CV(user_id=user_id, label=label, target_roles=target_roles or [], keywords=keywords or [], language=language)
        session.add(cv)
        session.flush()
        if len(list_cvs(session, user_id)) == 1:
            cv.is_default = True
    key, digest = storage.save(user_id, file_name, data)
    profile = parse_profile(llm, text)
    version = CVVersion(
        user_id=user_id, cv_id=cv.id, file_key=key, file_name=file_name, file_hash=digest,
        extracted_text=text, profile=profile, embedding=llm.embed([profile_text(profile) or text[:8000]])[0],
    )
    session.add(version)
    session.flush()
    cv.current_version_id = version.id
    session.flush()
    audit.record(session, user_id=user_id, actor="user", tool="upload_cv", entity_id=cv.id,
                 input={"label": cv.label, "file": file_name}, result={"version": str(version.id)})
    return cv


def update_cv(
    session: Session, llm: LLMClient, cv: CV, *, label: str | None = None, target_roles: list[str] | None = None,
    keywords: list[str] | None = None, language: str | None = None, is_default: bool | None = None,
    active: bool | None = None, profile: dict | None = None,
) -> CV:
    if label is not None:
        cv.label = label
    if target_roles is not None:
        cv.target_roles = target_roles
    if keywords is not None:
        cv.keywords = keywords
    if language is not None:
        cv.language = language
    if active is not None:
        cv.active = active
    if is_default:
        _set_default(session, cv.user_id, cv)
    if profile is not None:
        # A corrected profile becomes a new version so past applications keep what they sent.
        old = current_version(session, cv)
        version = CVVersion(
            user_id=cv.user_id, cv_id=cv.id, file_key=old.file_key, file_name=old.file_name, file_hash=old.file_hash,
            extracted_text=old.extracted_text, profile=profile, embedding=llm.embed([profile_text(profile)])[0],
        )
        session.add(version)
        session.flush()
        cv.current_version_id = version.id
    session.flush()
    return cv


def get_fact_sheet(session: Session, user_id: uuid.UUID) -> dict:
    row = session.execute(select(FactSheet).where(FactSheet.user_id == user_id)).scalar_one_or_none()
    return dict(row.data) if row else {}


def save_fact_sheet(session: Session, user_id: uuid.UUID, data: dict) -> dict:
    clean = {k: v for k, v in data.items() if k in FACT_SHEET_FIELDS}
    row = session.execute(select(FactSheet).where(FactSheet.user_id == user_id)).scalar_one_or_none()
    if row is None:
        row = FactSheet(user_id=user_id, data=clean)
        session.add(row)
    else:
        row.data = clean
    session.flush()
    audit.record(session, user_id=user_id, actor="user", tool="save_fact_sheet", input={"fields": sorted(clean)})
    return clean
