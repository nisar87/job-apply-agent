"""Append-only record of every action (master document, sections 4 and 11)."""

import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    session: Session,
    *,
    user_id: uuid.UUID,
    actor: str,
    tool: str,
    input: dict | None = None,
    result: dict | None = None,
    reason: str = "",
    run_id: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
    cost_usd: float = 0.0,
) -> AuditLog:
    row = AuditLog(
        user_id=user_id, actor=actor, tool=tool, input=_safe(input or {}), result=_safe(result or {}),
        reason=reason, run_id=run_id, entity_id=entity_id, cost_usd=cost_usd,
    )
    session.add(row)
    session.flush()
    return row


def _safe(value):
    """Make a value JSON-serialisable and keep audit rows small."""
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_safe(v) for v in list(value)[:50]]
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:2000] + "…"
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)
