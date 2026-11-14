"""Request dependencies: the signed-in user and a session limited to that user."""

import uuid
from collections.abc import Iterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import user_session
from app.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def current_user_id(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> uuid.UUID:
    user_id = decode_access_token(creds.credentials) if creds else None
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user_id


def db(user_id: uuid.UUID = Depends(current_user_id)) -> Iterator[Session]:
    with user_session(user_id) as session:
        yield session
