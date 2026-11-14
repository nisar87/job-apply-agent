"""File storage for CVs and attachments.

Local disk in development. In production point STORAGE_DIR at a mounted bucket or swap
this module for a Google Cloud Storage client; the rest of the code only uses these
three functions.
"""

import hashlib
import uuid
from pathlib import Path

from app.config import get_settings


def _root() -> Path:
    root = Path(get_settings().storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save(user_id: uuid.UUID, name: str, data: bytes) -> tuple[str, str]:
    """Store bytes; returns (key, sha256)."""
    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(name).suffix.lower()[:8]
    key = f"{user_id}/{digest}{suffix}"
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key, digest


def load(key: str) -> bytes:
    path = (_root() / key).resolve()
    if _root().resolve() not in path.parents:
        raise ValueError("Invalid storage key")
    return path.read_bytes()


def delete_user_files(user_id: uuid.UUID) -> None:
    folder = _root() / str(user_id)
    if folder.exists():
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
