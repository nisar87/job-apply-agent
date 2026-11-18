"""CVs, fact sheet, campaigns, setup chat, preferences and block lists."""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, storage
from app.agent import onboarding
from app.api.deps import current_user_id, db
from app.api.schemas import BlockOut, CampaignOut, CVOut
from app.llm import MeteredLLM, SpendCapExceeded
from app.models import CV, Block, Campaign, Preference
from app.services import campaigns as campaign_service
from app.services import cvs as cv_service
from app.services.campaigns import CampaignConfig

router = APIRouter(prefix="/api", tags=["core"])


def _llm(session: Session, user_id: uuid.UUID) -> MeteredLLM:
    """The user's model client on the key the admin's policy allows (raises NoModelKey, a SpendCapExceeded)."""
    from app.llm.access import llm_for

    return llm_for(session, user_id)


def _own(session: Session, model, item_id: uuid.UUID):
    row = session.get(model, item_id)
    if row is None:  # row-level security hides other users' rows, so this is also "not yours"
        raise HTTPException(404, "Not found")
    return row


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


# ---- CVs ----

@router.get("/cvs", response_model=list[CVOut])
def list_cvs(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    return cv_service.list_cvs(session, user_id)


@router.post("/cvs", response_model=CVOut, status_code=201)
async def upload_cv(
    file: UploadFile = File(...), label: str = Form(...), target_roles: str = Form(""), keywords: str = Form(""),
    language: str = Form("en"), cv_id: uuid.UUID | None = Form(None),
    user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function"),
):
    data = await file.read(cv_service.MAX_BYTES + 1)
    try:
        return cv_service.upload_cv(
            session, _llm(session, user_id), user_id, file_name=file.filename or "cv.pdf", data=data, label=label[:100],
            target_roles=_split(target_roles), keywords=_split(keywords), language=language[:16], cv_id=cv_id,
        )
    except cv_service.CVError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SpendCapExceeded as exc:
        raise HTTPException(429, str(exc)) from exc


class CVPatch(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    target_roles: list[str] | None = None
    keywords: list[str] | None = None
    language: str | None = None
    is_default: bool | None = None
    active: bool | None = None
    profile: dict | None = None


@router.patch("/cvs/{cv_id}", response_model=CVOut)
def patch_cv(cv_id: uuid.UUID, body: CVPatch, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    cv = _own(session, CV, cv_id)
    return cv_service.update_cv(session, _llm(session, user_id), cv, **body.model_dump(exclude_unset=True))


@router.get("/cvs/{cv_id}/profile")
def cv_profile(cv_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    version = cv_service.current_version(session, _own(session, CV, cv_id))
    return {"profile": version.profile if version else {}, "file_name": version.file_name if version else ""}


@router.get("/cvs/search-keywords")
def cv_search_keywords(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    """Search terms from the user's CVs, for filling a search with the skills they really have."""
    return {"keywords": cv_service.search_keywords(session, user_id)}


COMPANY_BOARDS_SHOWN = 300


@router.get("/job-sites")
def job_sites(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    """The places the agent looks for jobs, with how the last checks went. Shared directory, no user data.
    Every whole board, and of the tens of thousands of company boards the ones with the most matching jobs
    (the count of all of them is in /api/job-pool)."""
    from sqlalchemy import select as sa_select

    from app.models import JobSite

    boards = session.execute(sa_select(JobSite).where(JobSite.kind != "company")
                             .order_by(JobSite.provider)).scalars().all()
    companies = session.execute(sa_select(JobSite).where(JobSite.kind == "company").order_by(
        JobSite.last_matches.desc().nulls_last(), JobSite.last_ok_at.desc().nulls_last(), JobSite.slug,
    ).limit(COMPANY_BOARDS_SHOWN)).scalars().all()
    rows = [*boards, *companies]
    return [{"provider": r.provider, "slug": r.slug, "kind": r.kind, "name": r.name or r.provider, "url": r.url,
             "enabled": r.enabled, "last_checked_at": r.last_checked_at, "last_ok_at": r.last_ok_at,
             "last_matches": r.last_matches, "failures": r.failures, "last_error": r.last_error,
             "learned_from": r.learned_from} for r in rows]


@router.get("/job-pool")
def job_pool(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    """Size of the shared job pool and how the last harvest went (counts only; no user data)."""
    from app.services import harvest, pool

    last = harvest.last_result() or {}
    keep = ("started_at", "finished_at", "new", "boards_checked", "boards_learned", "boards_left", "expired",
            "sources", "errors")
    return {**pool.stats(session), "last_harvest": {k: last[k] for k in keep if k in last} or None}


@router.get("/countries")
def countries(user_id: uuid.UUID = Depends(current_user_id)):
    """Countries a search can be limited to, with the job sources that search each one."""
    from app.countries import catalogue

    return catalogue()


@router.get("/cv-versions/{version_id}/file")
def cv_file(version_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    from app.models import CVVersion

    version = _own(session, CVVersion, version_id)
    return Response(storage.load(version.file_key), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{version.file_name}"'})


# ---- Fact sheet ----

@router.get("/fact-sheet")
def get_fact_sheet(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    return cv_service.get_fact_sheet(session, user_id)


@router.put("/fact-sheet")
def put_fact_sheet(body: dict, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    return cv_service.save_fact_sheet(session, user_id, body)


# ---- Campaigns ----

class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: dict = Field(default_factory=dict)
    preferences: list[str] = Field(default_factory=list)
    state: str = "paused"
    # Automatic by default: every draft passes the code checks, the fact check and the hiring-manager review
    # (services/writing.py) before it is queued, and the daily report shows what went out.
    approval_mode: str = "full_auto"


class CampaignPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    config: dict | None = None
    state: str | None = None
    approval_mode: str | None = None
    auto_above_score: int | None = None


def _validated(config: dict) -> CampaignConfig:
    try:
        return CampaignConfig.model_validate(config)
    except ValidationError as exc:
        msg = "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())
        raise HTTPException(422, msg) from exc


@router.get("/campaigns", response_model=list[CampaignOut])
def list_campaigns(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    return campaign_service.list_campaigns(session, user_id)


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
def create_campaign(body: CampaignIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    if body.state not in ("active", "paused", "dry_run"):
        raise HTTPException(422, "Unknown state")
    if body.approval_mode not in ("review", "auto_above", "full_auto"):
        raise HTTPException(422, "Unknown approval mode")
    try:
        campaign = campaign_service.create_campaign(session, user_id, body.name, _validated(body.config), state=body.state,
                                                    approval_mode=body.approval_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    for text in body.preferences[:20]:
        session.add(Preference(user_id=user_id, campaign_id=campaign.id, text=text[:500], source="user"))
    session.flush()
    return campaign


@router.patch("/campaigns/{campaign_id}", response_model=CampaignOut)
def patch_campaign(campaign_id: uuid.UUID, body: CampaignPatch, session: Session = Depends(db, scope="function")):
    campaign = _own(session, Campaign, campaign_id)
    data = body.model_dump(exclude_unset=True)
    if "config" in data:
        data["config"] = _validated(data["config"])
    try:
        return campaign_service.update_campaign(session, campaign, **data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---- Preferences ----

class PreferenceIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    campaign_id: uuid.UUID | None = None


@router.get("/preferences")
def list_preferences(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    rows = session.execute(select(Preference).where(Preference.user_id == user_id).order_by(Preference.created_at.desc())).scalars()
    return [{"id": p.id, "text": p.text, "campaign_id": p.campaign_id, "source": p.source, "active": p.active} for p in rows]


@router.post("/preferences", status_code=201)
def add_preference(body: PreferenceIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    if body.campaign_id:
        _own(session, Campaign, body.campaign_id)
    pref = Preference(user_id=user_id, campaign_id=body.campaign_id, text=body.text, source="user")
    session.add(pref)
    session.flush()
    return {"id": pref.id}


@router.patch("/preferences/{pref_id}")
def toggle_preference(pref_id: uuid.UUID, active: bool, session: Session = Depends(db, scope="function")):
    pref = _own(session, Preference, pref_id)
    pref.active = active
    return {"id": pref.id, "active": pref.active}


# ---- Setup chat ----

class SetupTurnIn(BaseModel):
    history: list[dict] = Field(default_factory=list, max_length=60)
    draft: dict | None = None


@router.post("/setup/turn")
def setup_turn(body: SetupTurnIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    try:
        return onboarding.onboarding_turn(session, _llm(session, user_id), user_id, body.history, body.draft)
    except SpendCapExceeded as exc:
        raise HTTPException(429, str(exc)) from exc


@router.get("/setup/status")
def setup_status(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    return onboarding.setup_status(session, user_id)


# ---- Block lists ----

class BlockIn(BaseModel):
    type: str
    value: str = Field(min_length=1, max_length=320)
    reason: str = ""


@router.get("/blocks", response_model=list[BlockOut])
def list_blocks(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    return list(session.execute(select(Block).where(Block.user_id == user_id).order_by(Block.created_at)).scalars())


@router.post("/blocks", response_model=BlockOut, status_code=201)
def add_block(body: BlockIn, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    if body.type not in ("company", "domain", "address"):
        raise HTTPException(422, "Type must be company, domain or address")
    value = body.value.strip() if body.type == "company" else body.value.strip().lower()
    existing = session.execute(select(Block).where(Block.user_id == user_id, Block.type == body.type, Block.value == value)).scalar_one_or_none()
    if existing:
        return existing
    block = Block(user_id=user_id, type=body.type, value=value, reason=body.reason[:500])
    session.add(block)
    session.flush()
    audit.record(session, user_id=user_id, actor="user", tool="add_block", input={"type": body.type, "value": value})
    return block


@router.delete("/blocks/{block_id}", status_code=204)
def remove_block(block_id: uuid.UUID, session: Session = Depends(db, scope="function")):
    block = _own(session, Block, block_id)
    if not block.removable:
        raise HTTPException(409, "This entry was added because the employer asked not to be contacted. It cannot be removed.")
    session.delete(block)
