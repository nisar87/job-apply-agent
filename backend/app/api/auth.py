"""Sign-up, login and the current account.

Local email-and-password login sits behind these three endpoints only, so a hosted
identity provider (for example Firebase Authentication) can replace it later without
touching the rest of the application.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user_id, db
from app.config import get_settings
from app.db import system_session
from app.models import FactSheet, User
from app.security import create_access_token, hash_password, verify_password
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
    style_guide: str
    sample_emails: list


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    timezone: str | None = None
    escalation_email: EmailStr | None = None
    style_guide: str | None = Field(default=None, max_length=5000)
    sample_emails: list[str] | None = Field(default=None, max_length=3)


@router.post("/signup", response_model=TokenOut, status_code=201)
def signup(body: SignupIn):
    email = body.email.lower()
    if not get_settings().may_sign_up(email):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-up is by invitation only")
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


@router.get("/me", response_model=UserOut)
def me(user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found")
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: UserPatch, user_id: uuid.UUID = Depends(current_user_id), session: Session = Depends(db, scope="function")):
    user = session.get(User, user_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    session.flush()
    return user
