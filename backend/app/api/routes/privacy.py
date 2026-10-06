import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession, RedisDep, ShopAdmin
from app.core import rate_limit, signing
from app.models import Customer, Notification
from app.models.base import utcnow
from app.models.enums import ActorType
from app.services import audit

router = APIRouter(tags=["privacy"])

UNSUBSCRIBE_PURPOSE = "unsubscribe"


def unsubscribe_token(shop_id: uuid.UUID, customer_id: uuid.UUID) -> str:
    return signing.sign(UNSUBSCRIBE_PURPOSE, {"s": str(shop_id), "c": str(customer_id)})


class UnsubscribeBody(BaseModel):
    token: str = Field(min_length=10, max_length=512)


@router.post("/api/unsubscribe")
async def unsubscribe(
    body: UnsubscribeBody, request: Request, db: DbSession, redis: RedisDep
) -> dict[str, str]:
    """Public, one-click marketing opt-out from the link in every marketing email."""
    client = request.client.host if request.client else "unknown"
    if not await rate_limit.hit(redis, f"unsubscribe:{client}", 30):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")
    try:
        data = signing.unsign(UNSUBSCRIBE_PURPOSE, body.token)
    except signing.BadSignatureError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This unsubscribe link is invalid"
        ) from None
    customer = await db.scalar(
        select(Customer).where(
            Customer.id == uuid.UUID(data["c"]), Customer.shop_id == uuid.UUID(data["s"])
        )
    )
    if customer is None:
        return {"status": "unsubscribed"}
    if customer.unsubscribed_at is None:
        customer.unsubscribed_at = utcnow()
        customer.accepts_marketing = False
        audit.record(
            db,
            shop_id=customer.shop_id,
            actor_type=ActorType.SYSTEM,
            action="customer.unsubscribed",
            target_type="customer",
            target_id=str(customer.shopify_id),
        )
        await db.commit()
    return {"status": "unsubscribed"}


@router.get("/api/shops/{shop_id}/privacy/requests")
async def data_requests(ctx: ShopAdmin, db: DbSession) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(Notification)
            .where(
                Notification.shop_id == ctx.shop.id,
                Notification.kind == "privacy.data_request",
            )
            .order_by(Notification.created_at.desc())
            .limit(100)
        )
    ).all()
    return [
        {
            "id": str(n.id),
            "received_at": n.created_at.isoformat(),
            "customer_email": ((n.data.get("export") or {}).get("customer") or {}).get("email"),
            "orders": len((n.data.get("export") or {}).get("orders") or []),
        }
        for n in rows
    ]


@router.get("/api/shops/{shop_id}/privacy/requests/{request_id}/export.json")
async def data_request_export(request_id: uuid.UUID, ctx: ShopAdmin, db: DbSession) -> JSONResponse:
    note = await db.scalar(
        select(Notification).where(
            Notification.id == request_id,
            Notification.shop_id == ctx.shop.id,
            Notification.kind == "privacy.data_request",
        )
    )
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    return JSONResponse(
        note.data.get("export") or {},
        headers={"Content-Disposition": f'attachment; filename="data-request-{request_id}.json"'},
    )
