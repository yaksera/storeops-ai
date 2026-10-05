import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.models.enums import ActorType


def record(
    db: AsyncSession,
    *,
    shop_id: uuid.UUID,
    actor_type: ActorType,
    action: str,
    actor_id: str | uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Stage an audit entry in the caller's transaction so it commits atomically with the change."""
    entry = AuditLog(
        shop_id=shop_id,
        actor_type=actor_type,
        actor_id=str(actor_id) if actor_id is not None else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=details or {},
    )
    db.add(entry)
    return entry
