"""GDPR / CCPA obligations: customer data export, customer redaction and full shop deletion."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Checkout,
    Customer,
    Message,
    Notification,
    Order,
    Review,
    Shop,
    Ticket,
    WebhookEvent,
)
from app.models.base import utcnow
from app.models.enums import ActorType, MessageDirection, Severity, WebhookStatus
from app.services import audit

logger = logging.getLogger("storeops.privacy")

REDACTED = "[redacted]"
WEBHOOK_PAYLOAD_RETENTION = timedelta(days=30)


async def _customer(db: AsyncSession, shop: Shop, raw: dict[str, Any]) -> Customer | None:
    clauses = []
    if raw.get("id") is not None:
        clauses.append(Customer.shopify_id == int(raw["id"]))
    if raw.get("email"):
        clauses.append(func.lower(Customer.email) == str(raw["email"]).lower())
    if not clauses:
        return None
    return await db.scalar(
        select(Customer).where(Customer.shop_id == shop.id, or_(*clauses)).limit(1)
    )


async def export_customer_data(
    db: AsyncSession, shop: Shop, payload: dict[str, Any]
) -> dict[str, Any]:
    """Everything we hold about one customer, for the merchant to send on."""
    raw = payload.get("customer") or {}
    customer = await _customer(db, shop, raw)
    email = (customer.email if customer else None) or raw.get("email")
    orders = (
        await db.scalars(
            select(Order).where(
                Order.shop_id == shop.id,
                or_(
                    Order.customer_id == (customer.id if customer else None),
                    Order.email == email,
                    Order.shopify_id.in_([int(i) for i in payload.get("orders_requested") or []]),
                ),
            )
        )
    ).all()
    tickets = (
        (
            await db.scalars(
                select(Ticket).where(Ticket.shop_id == shop.id, Ticket.customer_email == email)
            )
        ).all()
        if email
        else []
    )
    messages = (
        (
            await db.scalars(select(Message).where(Message.ticket_id.in_([t.id for t in tickets])))
        ).all()
        if tickets
        else []
    )
    checkouts = (
        (
            await db.scalars(
                select(Checkout).where(Checkout.shop_id == shop.id, Checkout.email == email)
            )
        ).all()
        if email
        else []
    )
    export = {
        "request_id": (payload.get("data_request") or {}).get("id"),
        "generated_at": utcnow().isoformat(),
        "customer": (
            {
                "shopify_id": customer.shopify_id,
                "email": customer.email,
                "first_name": customer.first_name,
                "country_code": customer.country_code,
                "orders_count": customer.orders_count,
                "accepts_marketing": customer.accepts_marketing,
                "unsubscribed_at": customer.unsubscribed_at.isoformat()
                if customer.unsubscribed_at
                else None,
            }
            if customer
            else None
        ),
        "orders": [
            {
                "name": o.name,
                "total_minor": o.total_minor,
                "currency": o.currency,
                "processed_at": o.processed_at.isoformat(),
                "shipping_country": o.shipping_country,
                "risk_score": o.risk_score,
            }
            for o in orders
        ],
        "checkouts": [
            {
                "created_at": c.shopify_created_at.isoformat() if c.shopify_created_at else None,
                "status": c.status.value,
                "reminders_sent": c.reminders_sent,
            }
            for c in checkouts
        ],
        "support": [
            {"subject": t.subject, "messages": [m.body for m in messages if m.ticket_id == t.id]}
            for t in tickets
        ],
    }
    db.add(
        Notification(
            shop_id=shop.id,
            kind="privacy.data_request",
            severity=Severity.WARNING,
            title="Customer data request received",
            body=f"Shopify forwarded a data request for {email or 'a customer'}. "
            "Download the export from Settings → Privacy and send it to the customer.",
            data={"export": export},
        )
    )
    audit.record(
        db,
        shop_id=shop.id,
        actor_type=ActorType.SYSTEM,
        action="privacy.data_request",
        target_type="customer",
        target_id=str(customer.shopify_id) if customer else None,
        details={"orders": len(orders), "tickets": len(tickets)},
    )
    await db.flush()
    return export


async def redact_customer(db: AsyncSession, shop: Shop, payload: dict[str, Any]) -> dict[str, int]:
    raw = payload.get("customer") or {}
    customer = await _customer(db, shop, raw)
    email = (customer.email if customer else None) or raw.get("email")
    order_ids = [int(i) for i in payload.get("orders_to_redact") or []]
    now = utcnow()
    counts = {"orders": 0, "checkouts": 0, "tickets": 0}

    order_filter: list[ColumnElement[bool]] = [Order.shopify_id.in_(order_ids)] if order_ids else []
    if customer is not None:
        order_filter.append(Order.customer_id == customer.id)
    if email:
        order_filter.append(Order.email == email)
    if order_filter:
        result = await db.execute(
            update(Order)
            .where(Order.shop_id == shop.id, or_(*order_filter))
            .values(email=None, billing_country=None)
        )
        counts["orders"] = result.rowcount or 0  # type: ignore[attr-defined]
    if email:
        result = await db.execute(
            update(Checkout)
            .where(Checkout.shop_id == shop.id, Checkout.email == email)
            .values(email=None, recovery_url=None)
        )
        counts["checkouts"] = result.rowcount or 0  # type: ignore[attr-defined]
        tickets = (
            await db.scalars(
                select(Ticket).where(Ticket.shop_id == shop.id, Ticket.customer_email == email)
            )
        ).all()
        for ticket in tickets:
            ticket.customer_email = None
            await db.execute(
                update(Message)
                .where(
                    Message.ticket_id == ticket.id, Message.direction == MessageDirection.INBOUND
                )
                .values(body=REDACTED)
            )
        counts["tickets"] = len(tickets)
        await db.execute(
            update(Review)
            .where(Review.shop_id == shop.id, Review.author_name.ilike(f"%{email}%"))
            .values(author_name=None)
        )
    if customer is not None:
        customer.email = None
        customer.first_name = None
        customer.country_code = None
        customer.accepts_marketing = False
        customer.redacted_at = now
    audit.record(
        db,
        shop_id=shop.id,
        actor_type=ActorType.SYSTEM,
        action="privacy.customer_redacted",
        target_type="customer",
        target_id=str(raw.get("id")) if raw.get("id") is not None else None,
        details=counts,
    )
    await db.flush()
    return counts


async def purge_shop(db: AsyncSession, shop_id: uuid.UUID) -> bool:
    """Delete every row belonging to the shop, including its (otherwise append-only) audit log."""
    await db.execute(text("SET LOCAL storeops.audit_purge = 'on'"))
    result = await db.execute(delete(Shop).where(Shop.id == shop_id))
    await db.commit()
    if not result.rowcount:  # type: ignore[attr-defined]
        return False
    logger.info("shop purged", extra={"shop_id": str(shop_id)})
    return True


async def purge_due_shops(db: AsyncSession, now: datetime | None = None) -> int:
    now = now or utcnow()
    due = (
        await db.scalars(
            select(Shop.id).where(
                Shop.data_deletion_due_at.is_not(None), Shop.data_deletion_due_at < now
            )
        )
    ).all()
    for shop_id in due:
        await purge_shop(db, shop_id)
    return len(due)


async def apply_retention(db: AsyncSession, now: datetime | None = None) -> int:
    """Raw webhook payloads hold PII; keep them only as long as they're useful for debugging."""
    now = now or utcnow()
    result = await db.execute(
        delete(WebhookEvent).where(
            WebhookEvent.received_at < now - WEBHOOK_PAYLOAD_RETENTION,
            WebhookEvent.status.in_([WebhookStatus.PROCESSED, WebhookStatus.SKIPPED]),
        )
    )
    await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
