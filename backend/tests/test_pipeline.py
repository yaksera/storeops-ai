import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Checkout,
    InventorySnapshot,
    Message,
    Order,
    OrderItem,
    Review,
    Shop,
    Ticket,
    Variant,
    WebhookEvent,
)
from app.models.enums import CheckoutStatus, EventSource, TicketChannel, WebhookStatus
from app.pipeline.ingest import ingest_event
from app.services import events
from tests.conftest import InlineQueue


async def _shop(db: AsyncSession, shop: dict[str, str]) -> Shop:
    row = await db.get(Shop, uuid.UUID(shop["id"]))
    assert row is not None
    return row


async def _variant(db: AsyncSession, shop_id: uuid.UUID, sku: str) -> Variant:
    variant = await db.scalar(select(Variant).where(Variant.shop_id == shop_id, Variant.sku == sku))
    assert variant is not None
    return variant


async def _ingest(
    db: AsyncSession,
    queue: InlineQueue,
    shop_id: uuid.UUID,
    topic: str,
    payload: dict[str, Any],
    webhook_id: str | None = None,
) -> uuid.UUID | None:
    result = await ingest_event(
        db,
        queue,
        shop_id=shop_id,
        topic=topic,
        webhook_id=webhook_id or uuid.uuid4().hex,
        payload=payload,
        source=EventSource.SHOPIFY,
    )
    return result.event_id


def _order_payload(
    variant: Variant, *, order_id: int, updated: datetime, **extra: Any
) -> dict[str, Any]:
    return {
        "id": order_id,
        "name": "#9001",
        "email": "kai@customers.example",
        "created_at": updated.isoformat(),
        "updated_at": updated.isoformat(),
        "currency": "USD",
        "subtotal_price": "249.00",
        "total_discounts": "0.00",
        "total_price": "249.00",
        "financial_status": "paid",
        "checkout_token": "tok-123",
        "source_name": "web",
        "customer": {
            "id": 555,
            "email": "kai@customers.example",
            "first_name": "Kai",
            "orders_count": 1,
            "default_address": {"country_code": "US"},
        },
        "shipping_address": {"country_code": "US"},
        "billing_address": {"country_code": "US"},
        "line_items": [
            {
                "id": 1,
                "variant_id": variant.shopify_id,
                "title": "Ridgeline Down Jacket - M",
                "sku": variant.sku,
                "quantity": 1,
                "price": "249.00",
            }
        ],
        **extra,
    }


async def test_duplicate_webhook_is_ignored(
    db: AsyncSession, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    payload = {"token": "dup-1", "total_price": "10.00", "line_items": []}
    first = await ingest_event(
        db,
        queue,
        shop_id=shop.id,
        topic="checkouts/create",
        webhook_id="wh-dup",
        payload=payload,
        source=EventSource.SHOPIFY,
    )
    second = await ingest_event(
        db,
        queue,
        shop_id=shop.id,
        topic="checkouts/create",
        webhook_id="wh-dup",
        payload=payload,
        source=EventSource.SHOPIFY,
    )
    assert not first.duplicate
    assert second.duplicate
    count = await db.scalar(select(func.count()).select_from(WebhookEvent))
    assert count == 1
    assert [job[0] for job in queue.jobs] == ["process_webhook_event"]
    event = await db.get(WebhookEvent, first.event_id)
    assert event is not None
    await db.refresh(event)
    assert event.status == WebhookStatus.PROCESSED


async def test_checkout_to_order_flow_updates_tables_and_stream(
    db: AsyncSession,
    queue: InlineQueue,
    redis: fakeredis.FakeAsyncRedis,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    variant = await _variant(db, shop.id, "NB-RDJ-M")
    start_qty = variant.inventory_quantity
    now = datetime.now(UTC)

    await _ingest(
        db,
        queue,
        shop.id,
        "checkouts/create",
        {
            "id": 77,
            "token": "tok-123",
            "email": "kai@customers.example",
            "total_price": "249.00",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "line_items": [{"variant_id": variant.shopify_id, "quantity": 1, "price": "249.00"}],
        },
    )
    await _ingest(
        db,
        queue,
        shop.id,
        "orders/create",
        _order_payload(variant, order_id=880001, updated=now),
    )
    await _ingest(
        db,
        queue,
        shop.id,
        "inventory_levels/update",
        {"inventory_item_id": variant.inventory_item_id, "available": start_qty - 1},
    )

    order = await db.scalar(select(Order).where(Order.shopify_id == 880001))
    assert order is not None
    assert order.total_minor == 24900
    assert order.shipping_country == "US"
    items = (await db.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all()
    assert [(i.variant_id, i.quantity) for i in items] == [(variant.id, 1)]

    checkout = await db.scalar(select(Checkout).where(Checkout.token == "tok-123"))
    assert checkout is not None
    assert checkout.status == CheckoutStatus.COMPLETED

    await db.refresh(variant)
    assert variant.inventory_quantity == start_qty - 1
    snapshot = await db.scalar(
        select(InventorySnapshot).where(InventorySnapshot.variant_id == variant.id)
    )
    assert snapshot is not None
    assert snapshot.delta == -1

    published = await events.recent(redis, shop.id, 50)
    types = [e.type for e in reversed(published)]
    assert "order.created" in types
    assert "inventory.changed" in types
    assert "kpis.updated" in types
    assert "agent.status" in types
    activity = [e for e in published if e.type == "activity"]
    order_activity = next(a for a in activity if a.data["kind"] == "order.created")
    assert order_activity.data["title"] == "New order #9001 · $249.00"
    assert "Fraud Guard" in order_activity.data["detail"]
    kpis = next(e for e in published if e.type == "kpis.updated")
    assert kpis.data["orders"] >= 1


async def test_out_of_order_updates_are_skipped(
    db: AsyncSession, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    variant = await _variant(db, shop.id, "NB-RDJ-M")
    newer = datetime.now(UTC)
    older = newer - timedelta(minutes=5)

    await _ingest(
        db, queue, shop.id, "orders/create", _order_payload(variant, order_id=1, updated=newer)
    )
    await _ingest(
        db,
        queue,
        shop.id,
        "orders/updated",
        _order_payload(variant, order_id=1, updated=newer, financial_status="refunded"),
    )
    stale_id = await _ingest(
        db,
        queue,
        shop.id,
        "orders/updated",
        _order_payload(variant, order_id=1, updated=older, financial_status="pending"),
    )
    order = await db.scalar(select(Order).where(Order.shop_id == shop.id, Order.shopify_id == 1))
    assert order is not None
    assert order.financial_status == "refunded"
    stale = await db.get(WebhookEvent, stale_id)
    assert stale is not None
    assert stale.status == WebhookStatus.SKIPPED
    assert (
        await db.scalar(
            select(func.count()).select_from(OrderItem).where(OrderItem.order_id == order.id)
        )
        == 1
    )


async def test_refund_fulfillment_review_and_support(
    db: AsyncSession, queue: InlineQueue, redis: fakeredis.FakeAsyncRedis, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    variant = await _variant(db, shop.id, "NB-RDJ-M")
    now = datetime.now(UTC)
    await _ingest(
        db, queue, shop.id, "orders/create", _order_payload(variant, order_id=42, updated=now)
    )
    await _ingest(
        db,
        queue,
        shop.id,
        "refunds/create",
        {"order_id": 42, "transactions": [{"kind": "refund", "amount": "49.00"}]},
    )
    await _ingest(db, queue, shop.id, "fulfillments/create", {"order_id": 42, "status": "success"})
    await _ingest(
        db,
        queue,
        shop.id,
        "reviews/create",
        {"id": "r1", "source": "judgeme", "rating": 1, "title": "Seam leaked", "body": "Wet."},
    )
    await _ingest(
        db,
        queue,
        shop.id,
        "support/message",
        {
            "id": "m1",
            "from_email": "kai@customers.example",
            "first_name": "Kai",
            "subject": "Where is my order?",
            "body": "Any update on #9001?",
            "order_name": "#9001",
        },
    )

    order = await db.scalar(select(Order).where(Order.shop_id == shop.id, Order.shopify_id == 42))
    assert order is not None
    assert order.refunded_minor == 4900
    assert order.financial_status == "partially_refunded"
    assert order.fulfillment_status == "fulfilled"

    review = await db.scalar(select(Review).where(Review.external_id == "r1"))
    assert review is not None
    assert review.rating == 1

    ticket = await db.scalar(
        select(Ticket).where(Ticket.shop_id == shop.id, Ticket.channel == TicketChannel.EMAIL)
    )
    assert ticket is not None
    assert ticket.order_id == order.id
    message = await db.scalar(select(Message).where(Message.ticket_id == ticket.id))
    assert message is not None
    assert message.body == "Any update on #9001?"

    activity = [e.data for e in await events.recent(redis, shop.id, 50, {"activity"})]
    titles = [a["title"] for a in activity]
    assert "Refund on #9001 · $49.00" in titles
    assert "1★ review" in titles
    review_activity = next(a for a in activity if a["kind"] == "review.created")
    assert review_activity["severity"] == "warning"


async def test_failed_event_is_marked_and_isolated(
    db: AsyncSession, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    event_id = await _ingest(db, queue, shop.id, "orders/create", {"name": "missing id"})
    event = await db.get(WebhookEvent, event_id)
    assert event is not None
    assert event.status == WebhookStatus.FAILED
    assert event.error
    assert "KeyError" in event.error

    ok_id = await _ingest(
        db, queue, shop.id, "checkouts/create", {"token": "t-ok", "line_items": []}
    )
    ok = await db.get(WebhookEvent, ok_id)
    assert ok is not None
    assert ok.status == WebhookStatus.PROCESSED


async def test_unknown_topic_is_accepted_without_changes(
    db: AsyncSession, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    event_id = await _ingest(db, queue, shop.id, "carts/update", {"id": 1})
    event = await db.get(WebhookEvent, event_id)
    assert event is not None
    assert event.status == WebhookStatus.PROCESSED
