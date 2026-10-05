import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, select

from app.api.deps import DbSession, ShopViewer
from app.models import AuditLog
from app.models.enums import ActorType

router = APIRouter(prefix="/api/shops/{shop_id}/audit", tags=["audit"])


def _filtered(
    ctx: ShopViewer,
    action: str | None,
    actor_type: ActorType | None,
    since: datetime | None,
    until: datetime | None,
    q: str | None,
) -> Select[AuditLog]:
    query = select(AuditLog).where(AuditLog.shop_id == ctx.shop.id)
    if action:
        query = query.where(AuditLog.action.startswith(action))
    if actor_type:
        query = query.where(AuditLog.actor_type == actor_type)
    if since:
        query = query.where(AuditLog.occurred_at >= since)
    if until:
        query = query.where(AuditLog.occurred_at < until)
    if q:
        query = query.where(AuditLog.details["title"].astext.ilike(f"%{q}%"))
    return query


def _row(entry: AuditLog) -> dict[str, Any]:
    return {
        "id": entry.id,
        "occurred_at": entry.occurred_at.isoformat(),
        "actor_type": entry.actor_type.value,
        "actor_id": entry.actor_id,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "details": entry.details,
    }


@router.get("")
async def list_audit(
    ctx: ShopViewer,
    db: DbSession,
    action: Annotated[str | None, Query(max_length=64)] = None,
    actor_type: ActorType | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    query = _filtered(ctx, action, actor_type, since, until, q)
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    rows = (await db.scalars(query.order_by(AuditLog.id.desc()).limit(limit + 1))).all()
    items = [_row(r) for r in rows[:limit]]
    return {"items": items, "next_before_id": items[-1]["id"] if len(rows) > limit else None}


@router.get("/export.csv")
async def export_audit(
    ctx: ShopViewer,
    db: DbSession,
    action: Annotated[str | None, Query(max_length=64)] = None,
    actor_type: ActorType | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> StreamingResponse:
    query = _filtered(ctx, action, actor_type, since, until, q).order_by(AuditLog.id)
    rows = (await db.scalars(query.limit(50_000))).all()
    await db.close()

    async def generate() -> AsyncIterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "occurred_at",
                "actor_type",
                "actor_id",
                "action",
                "target_type",
                "target_id",
                "details",
            ]
        )
        for entry in rows:
            writer.writerow(
                [
                    entry.id,
                    entry.occurred_at.isoformat(),
                    entry.actor_type.value,
                    entry.actor_id or "",
                    entry.action,
                    entry.target_type or "",
                    entry.target_id or "",
                    json.dumps(entry.details, default=str),
                ]
            )
            if buffer.tell() > 64_000:
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate()
        yield buffer.getvalue()

    filename = f"audit-{ctx.shop.domain}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
