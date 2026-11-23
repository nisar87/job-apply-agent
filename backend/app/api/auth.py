"""Sign-up, login and the current account.

Local email-and-password login sits behind these three endpoints only, so a hosted
identity provider (for example Firebase Authentication) can replace it later without
touching the rest of the application.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user_id, db
from app.config import get_settings
from app.db import system_session
from app.models import FactSheet, User
from app.security import (
    create_access_token,
    hash_password,
    read_reset_token,
    reset_token_matches,
    verify_password,
)
from app.services import account
from app.services import signup as signup_rules
from app.tasks.locks import get_redis

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    name: str = Field(default="", max_length=200)
    timezone: str = "UTC"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    timezone: str
    plan: str
    escalation_email: str | None
    report_frequency: str = "daily"
    report_hour: int = 20
    style_guide: str
    sample_emails: list
    is_admin: bool = False
    environment: str = "development"   # production | staging | development | test
    sends_for_real: bool = False       # true when this user's applications reach employers


def _user_out(user: User) -> UserOut:
    out = UserOut.model_validate(user, from_attributes=True)
    out.is_admin = bool(user.is_operator or get_settings().is_admin_email(user.email))
    from app.services import live

    out.environment = get_settings().app_env
    out.sends_for_real = live.sends_for_real(user.id)
    return out


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    timezone: str | None = None
    escalation_email: EmailStr | None = None
    report_frequency: Literal["daily", "weekly", "off"] | None = None
    report_hour: int | None = Field(default=None, ge=0, le=23)
    style_guide: str | None = Field(default=None, max_length=5000)
    sample_emails: list[str] | None = Field(default=None, max_length=3)


@router.post("/signup", response_model=TokenOut, status_code=201)
def signup(body: SignupIn):
    email = body.email.lower()
    if not signup_rules.may_sign_up(email):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-up is by invitation only. Ask the person who runs this service to invite you.")
    with system_session() as session:
        if session.execute(select(User).where(User.email == email)).scalar_one_or_none():
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
        user = User(email=email, password_hash=hash_password(body.password), name=body.name, timezone=body.timezone)
        session.add(user)
        session.flush()
        session.add(FactSheet(user_id=user.id, data={}))
        audit.record(session, user_id=user.id, actor="user", tool="signup")
        return TokenOut(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn):
    # Ten wrong passwords for one address lock sign-in for 15 minutes.
    key = f"rl:login:{body.email.lower()}"
    r = get_redis()
    if int(r.get(key) or 0) >= 10:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in 15 minutes.")
    with system_session() as session:
        user = session.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
        if not user or user.status == "deleted" or not verify_password(body.password, user.password_hash):
            if r.incr(key) == 1:
                r.expire(key, 900)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
        r.delete(key)
        return TokenOut(access_token=create_access_token(user.id))


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(max_length=2000)
    password: str = Field(min_length=10, max_length=200)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


@router.post("/forgot")
def forgot_password(body: ForgotIn):
    """Email a reset link. The answer is the same whether or not the address has an account."""
    email = body.email.lower()
    key = f"rl:forgot:{email}"
    r = get_redis()
    count = r.incr(key)
    if count == 1:
        r.expire(key, 3600)
    if count <= 3:
        with system_session() as session:
            user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if user and user.status != "deleted":
                sent = account.send_reset_link(session, user)
                audit.record(session, user_id=user.id, actor="user", tool="password_reset_requested",
                             result={"emailed": sent})
    return {"ok": True}


@router.post("/reset", response_model=TokenOut)
def reset_password(body: ResetIn):
    found = read_reset_token(body.token)
    expired = HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link has expired or was already used. Ask for a new one.")
    if not found:
        raise expired
    user_id, fingerprint = found
    with system_session() as session:
        user = session.get(User, user_id)
        if not user or user.status == "deleted" or not reset_token_matches(fingerprint, user.password_hash):
            raise expired
        user.password_hash = hash_password(body.password)
        audit.record(session, user_id=user.id, actor="user", tool="password_reset")
        get_redis().delete(f"rl:login:{user.email}")
        return TokenOut(access_token=create_access_token(user.id))


@router.post("/password")
def change_password(body: PasswordIn, user_id: uuid.UUID = Depends(current_user_id)):
    with system_session() as session:
        user = session.get(User, user_id)
        if not user or not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is not right")
        user.password_hash = hash_password(body.new_password)
        audit.record(session, user_id=user.id, actor="user", tool="password_changed")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found")
    return _user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(body: UserPatch, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    user = session.get(User, user_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    session.flush()
    return _user_out(user)
