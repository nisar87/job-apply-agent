"""CV intake, structured profiles and the fact sheet (master document, section 8)."""

import io
import re
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
        "country": {"type": "string", "description": "ISO 3166-1 alpha-2 code (lower case) of the country the "
                                                     "candidate lives in, if the CV shows it (city, address or "
                                                     "phone prefix); otherwise empty"},
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
        "search_keywords": {
            "type": "array", "items": {"type": "string"},
            "description": "Up to 12 short search terms a matching job posting would contain, taken only from what "
                           "the CV shows: languages, frameworks, databases and tools, plus the platform they belong "
                           "to (for example '.NET' as well as 'C#', 'Java' as well as 'Spring'). Prefer technologies "
                           "over job titles; never a generic title alone such as 'Software Engineer'. No soft skills.",
        },
    },
    "required": ["name", "summary", "skills", "roles"],
}

FACT_SHEET_FIELDS = [
    "home_country",  # ISO code of where the candidate lives: remote jobs that cannot hire there are skipped
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
    """Flat text of a profile, used for embeddings, prompts and the fact check. It must carry every
    fact an application may state, or the fact check rejects true claims."""
    def join(*values) -> str:
        return " ".join(str(v) for v in values if v not in (None, "", []))

    parts = [join("Name:", profile.get("name")) if profile.get("name") else "",
             join("Location:", profile.get("location")) if profile.get("location") else "",
             profile.get("summary") or ""]
    if profile.get("total_years_experience"):
        parts.append(f"Total professional experience: {profile['total_years_experience']:g} years")
    skills = [join(s.get("name"), f"({s['years']:g} years)" if s.get("years") else "") for s in profile.get("skills") or []
              if isinstance(s, dict)]
    if skills:
        parts.append("Skills: " + ", ".join(skills))
    for role in profile.get("roles") or []:
        period = join(role.get("start"), "to", role.get("end") or "today") if role.get("start") else join(role.get("end"))
        parts.append(join(role.get("title"), "at", role.get("company")) + (f" ({period})" if period else ""))
        parts += [a for a in role.get("achievements") or [] if a]
    langs = [join(lang.get("name"), lang.get("level")) for lang in profile.get("languages") or [] if isinstance(lang, dict)]
    if langs:
        parts.append("Languages: " + ", ".join(langs))
    parts += [e for e in profile.get("education") or [] if e]
    parts += [c for c in profile.get("certifications") or [] if c]
    return "\n".join(p for p in parts if p)


MAX_SEARCH_KEYWORDS = 12
# Skills that describe how someone works, not what a posting searches for. Used only when a profile
# was parsed before it carried its own search keywords.
_SOFT_SKILLS = {
    "problem solving", "problem-solving", "communication", "leadership", "teamwork", "team player",
    "time management", "collaboration", "critical thinking", "adaptability", "creativity", "attention to detail",
    "self-motivated", "mentoring", "stakeholder management", "presentation skills",
}


_VENDOR = re.compile(r"^(microsoft|google|amazon|oracle|apple|ibm)\s+", re.IGNORECASE)


def _forms(term: str) -> list[str]:
    """The term as postings write it: "Microsoft SQL Server" is searched as "SQL Server", and a
    .NET technology ("ASP.NET", "VB.NET") also brings in the platform ".NET"."""
    term = _VENDOR.sub("", " ".join(term.split()).strip(" -"))
    forms = [term]
    if re.search(r"\w\.net\b", term, re.IGNORECASE):
        forms.append(".NET")
    return forms


def search_keywords_of(profile: dict) -> list[str]:
    """Search terms for one CV profile: what the person can do, never what they cannot.

    The terms read from the CV (`search_keywords`) come first, then the profile's skills, which may
    have been added after parsing. Skills are split into single terms ("C# and VB.NET" becomes
    "C#", "VB.NET") and soft skills are left out."""
    raw = [str(k) for k in profile.get("search_keywords") or [] if str(k).strip()]
    for skill in profile.get("skills") or []:
        name = str(skill.get("name", "") if isinstance(skill, dict) else skill)
        name = re.sub(r"\(.*?\)", " ", name)
        raw += re.split(r"\s*(?:,|/|;|\band\b|&)\s*", name)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        for term in _forms(item):
            key = term.lower()
            if not term or len(term) > 40 or key in seen or key in _SOFT_SKILLS:
                continue
            seen.add(key)
            out.append(term)
    return out[:MAX_SEARCH_KEYWORDS]


def search_keywords(session: Session, user_id: uuid.UUID) -> list[str]:
    """Search terms from the user's active CVs, default CV first. Used when a search has no keywords
    of its own, so the agent looks for jobs that fit the skills the user really has."""
    cvs = sorted(list_cvs(session, user_id, active_only=True), key=lambda c: not c.is_default)
    out: list[str] = []
    for cv in cvs:
        version = current_version(session, cv)
        if version is None:
            continue
        for term in [*(cv.keywords or []), *search_keywords_of(version.profile or {})]:
            if term.lower() not in {t.lower() for t in out}:
                out.append(term)
    return out[:MAX_SEARCH_KEYWORDS]


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
    prefill_fact_sheet(session, user_id, profile)
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


def prefill_fact_sheet(session: Session, user_id: uuid.UUID, profile: dict) -> list[str]:
    """Fill fact sheet fields the CV already answers (home country, location, phone, languages), so the agent
    can use them without the user typing them again. Only empty fields are filled; nothing is overwritten."""
    from app.countries import normalize

    langs = [" ".join(str(x) for x in (lang.get("name"), lang.get("level")) if x)
             for lang in profile.get("languages") or [] if isinstance(lang, dict) and lang.get("name")]
    found = {"home_country": normalize(profile.get("country") or "") or "", "location": profile.get("location") or "",
             "phone": profile.get("phone") or "", "languages": ", ".join(langs)}
    row = session.execute(select(FactSheet).where(FactSheet.user_id == user_id)).scalar_one_or_none()
    data = dict(row.data) if row else {}
    filled = [k for k, v in found.items() if v and not data.get(k)]
    if not filled:
        return []
    data.update({k: found[k] for k in filled})
    if row is None:
        session.add(FactSheet(user_id=user_id, data=data))
    else:
        row.data = data
    session.flush()
    audit.record(session, user_id=user_id, actor="system", tool="prefill_fact_sheet", input={"fields": filled},
                 reason="taken from the CV")
    return filled


def save_fact_sheet(session: Session, user_id: uuid.UUID, data: dict) -> dict:
    clean = {k: v for k, v in data.items() if k in FACT_SHEET_FIELDS}
    if clean.get("home_country"):
        from app.countries import normalize

        clean["home_country"] = normalize(str(clean["home_country"])) or ""
    row = session.execute(select(FactSheet).where(FactSheet.user_id == user_id)).scalar_one_or_none()
    if row is None:
        row = FactSheet(user_id=user_id, data=clean)
        session.add(row)
    else:
        row.data = clean
    session.flush()
    audit.record(session, user_id=user_id, actor="user", tool="save_fact_sheet", input={"fields": sorted(clean)})
    return clean
