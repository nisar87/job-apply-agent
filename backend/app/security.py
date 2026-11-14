"""Password hashing, login tokens and encryption of stored secrets."""

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

from app.config import get_settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, Exception):
        return False


def create_access_token(user_id: uuid.UUID) -> str:
    s = get_settings()
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(UTC) + timedelta(minutes=s.access_token_minutes),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> uuid.UUID | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        return uuid.UUID(payload["sub"])
    except Exception:
        return None


def _fernet() -> Fernet:
    s = get_settings()
    key = s.token_encryption_key
    if not key:
        if s.is_production:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set in production")
        # Development fallback derived from the secret key. Never used in production.
        key = base64.urlsafe_b64encode(hashlib.sha256(s.secret_key.encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
