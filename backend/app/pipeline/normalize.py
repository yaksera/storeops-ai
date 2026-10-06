"""Normalise Shopify-shaped webhook payloads into domain tables.

Handlers are idempotent upserts keyed on Shopify ids, and ignore payloads older than what is
already stored (`updated_at`), so duplicate and out-of-order deliveries are harmless.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Checkout,
    Customer,
    InventorySnapshot,
    Message,
    Order,
    OrderItem,
    Product,
    Review,
    Shop,
    Ticket,
    Variant,
)
from app.models.base import utcnow
from app.models.enums import (
    CheckoutStatus,
    MessageAuthor,
    MessageDirection,
    ReviewSource,
    TicketChannel,
)

Payload = dict[str, Any]


@dataclass(slots=True)
class Change:
    """A domain change worth telling the dashboard (and, from M3, the agents) about."""

    kind: str
    data: dict[str, Any]


@dataclass(slots=True)
class NormalizeResult:
    changes: list[Change] = field(default_factory=list)
    stale: bool = False


Handler = Callable[[AsyncSession, Shop, Payload], Awaitable[NormalizeResult]]


def to_minor(amount: Any) -> int:
    if amount in (None, ""):
        return 0
    try:
        return int((Decimal(str(amount)) * 100).quantize(Decimal(1)))
    except InvalidOperation:
        return 0


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else None


def _is_stale(stored: datetime | None, incoming: datetime | None) -> bool:
    return stored is not None and incoming is not None and incoming < stored


def _country(address: Any) -> str | None:
    if isinstance(address, dict):
        code = address.get("country_code")
        return str(code)[:2] if code else None
    return None


async def _upsert_customer(db: AsyncSession, shop: Shop, raw: Any) -> Customer | None:
    if not isinstance(raw, dict) or raw.get("id") is None:
        return None
    customer = await db.scalar(
        select(Customer).where(Customer.shop_id == shop.id, Customer.shopify_id == int(raw["id"]))
    )
    if customer is None:
        customer = Customer(shop_id=shop.id, shopify_id=int(raw["id"]))
        db.add(customer)
    customer.email = raw.get("email") or customer.email
    customer.first_name = raw.get("first_name") or customer.first_name
    customer.country_code = _country(raw.get("default_address")) or customer.country_code
    if raw.get("orders_count") is not None:
        customer.orders_count = int(raw["orders_count"])
    if raw.get("total_spent") is not None:
        customer.total_spent_minor = to_minor(raw["total_spent"])
    if raw.get("accepts_marketing") is not None:
        customer.accepts_marketing = bool(raw["accepts_marketing"])
    await db.flush()
    return customer


def order_summary(order: Order, customer: Customer | None, item_count: int) -> dict[str, Any]:
    return {
        "id": str(order.id),
        "shopify_id": order.shopify_id,
        "name": order.name,
        "total_minor": order.total_minor,
        "refunded_minor": order.refunded_minor,
        "currency": order.currency,
        "customer_name": customer.first_name if customer else None,
        "customer_orders": customer.orders_count if customer else None,
        "shipping_country": order.shipping_country,
        "billing_country": order.billing_country,
        "item_count": item_count,
        "financial_status": order.financial_status,
        "fulfillment_status": order.fulfillment_status,
        "source": order.source_name,
        "risk_score": order.risk_score,
        "is_held": order.is_held,
        "cancelled": order.cancelled_at is not None,
        "processed_at": order.processed_at.isoformat(),
    }


async def handle_order(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    shopify_id = int(p["id"])
    updated_at = parse_ts(p.get("updated_at"))
    order = await db.scalar(
        select(Order).where(Order.shop_id == shop.id, Order.shopify_id == shopify_id)
    )
    if order is not None and _is_stale(order.shopify_updated_at, updated_at):
        return NormalizeResult(stale=True)

    created = order is None
    customer = await _upsert_customer(db, shop, p.get("customer"))
    if order is None:
        order = Order(
            shop_id=shop.id,
            shopify_id=shopify_id,
            processed_at=parse_ts(p.get("processed_at") or p.get("created_at")) or utcnow(),
        )
        db.add(order)
    order.name = str(p.get("name") or f"#{shopify_id}")
    order.customer_id = customer.id if customer else None
    order.email = p.get("email")
    order.checkout_token = p.get("checkout_token")
    order.currency = str(p.get("currency") or shop.currency)
    order.subtotal_minor = to_minor(p.get("subtotal_price"))
    order.discount_minor = to_minor(p.get("total_discounts"))
    order.total_minor = to_minor(p.get("total_price"))
    order.financial_status = p.get("financial_status")
    order.fulfillment_status = p.get("fulfillment_status")
    order.shipping_country = _country(p.get("shipping_address"))
    order.billing_country = _country(p.get("billing_address"))
    order.source_name = p.get("source_name")
    order.cancelled_at = parse_ts(p.get("cancelled_at"))
    order.shopify_updated_at = updated_at
    await db.flush()

    line_items = p.get("line_items") or []
    if created:
        variant_ids = [int(li["variant_id"]) for li in line_items if li.get("variant_id")]
        variants = {
            v.shopify_id: v
            for v in (
                await db.scalars(
                    select(Variant).where(
                        Variant.shop_id == shop.id, Variant.shopify_id.in_(variant_ids)
                    )
                )
            ).all()
        }
        for li in line_items:
            variant = variants.get(int(li["variant_id"])) if li.get("variant_id") else None
            db.add(
                OrderItem(
                    shop_id=shop.id,
                    order_id=order.id,
                    variant_id=variant.id if variant else None,
                    shopify_line_item_id=li.get("id"),
                    title=str(li.get("title") or "Item"),
                    sku=li.get("sku"),
                    quantity=int(li.get("quantity") or 1),
                    price_minor=to_minor(li.get("price")),
                )
            )

    if created and order.checkout_token:
        checkout = await db.scalar(
            select(Checkout).where(
                Checkout.shop_id == shop.id, Checkout.token == order.checkout_token
            )
        )
        if checkout is not None and checkout.status != CheckoutStatus.COMPLETED:
            checkout.status = (
                CheckoutStatus.RECOVERED
                if checkout.reminders_sent > 0
                else CheckoutStatus.COMPLETED
            )
            checkout.completed_at = order.processed_at
            checkout.recovered_order_id = order.id if checkout.reminders_sent > 0 else None

    await db.flush()
    summary = order_summary(order, customer, sum(int(li.get("quantity") or 1) for li in line_items))
    return NormalizeResult(
        changes=[Change("order.created" if created else "order.updated", summary)]
    )


async def handle_order_cancelled(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    p = {**p, "cancelled_at": p.get("cancelled_at") or utcnow().isoformat()}
    return await handle_order(db, shop, p)


async def handle_refund(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    order = await db.scalar(
        select(Order).where(Order.shop_id == shop.id, Order.shopify_id == int(p["order_id"]))
    )
    if order is None:
        return NormalizeResult(stale=True)
    amount = sum(
        to_minor(t.get("amount"))
        for t in p.get("transactions") or []
        if t.get("kind", "refund") == "refund"
    )
    order.refunded_minor = min(order.total_minor, order.refunded_minor + amount)
    order.financial_status = (
        "refunded" if order.refunded_minor >= order.total_minor else "partially_refunded"
    )
    await db.flush()
    customer = await db.get(Customer, order.customer_id) if order.customer_id else None
    data = order_summary(order, customer, 0) | {"refund_minor": amount}
    return NormalizeResult(changes=[Change("order.refunded", data)])


async def handle_fulfillment(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    order = await db.scalar(
        select(Order).where(Order.shop_id == shop.id, Order.shopify_id == int(p["order_id"]))
    )
    if order is None:
        return NormalizeResult(stale=True)
    order.fulfillment_status = "fulfilled" if p.get("status", "success") == "success" else "partial"
    await db.flush()
    customer = await db.get(Customer, order.customer_id) if order.customer_id else None
    data = order_summary(order, customer, 0) | {"tracking_number": p.get("tracking_number")}
    return NormalizeResult(changes=[Change("order.fulfilled", data)])


async def handle_product(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    updated_at = parse_ts(p.get("updated_at"))
    product = await db.scalar(
        select(Product).where(Product.shop_id == shop.id, Product.shopify_id == int(p["id"]))
    )
    if product is not None and _is_stale(product.shopify_updated_at, updated_at):
        return NormalizeResult(stale=True)
    if product is None:
        product = Product(shop_id=shop.id, shopify_id=int(p["id"]))
        db.add(product)
    product.title = str(p.get("title") or "Untitled")
    product.handle = str(p.get("handle") or product.title.lower().replace(" ", "-"))
    product.vendor = p.get("vendor")
    product.product_type = p.get("product_type")
    product.status = str(p.get("status") or "active")
    image = p.get("image")
    product.image_url = image.get("src") if isinstance(image, dict) else None
    product.shopify_updated_at = updated_at
    await db.flush()
    for raw in p.get("variants") or []:
        variant = await db.scalar(
            select(Variant).where(Variant.shop_id == shop.id, Variant.shopify_id == int(raw["id"]))
        )
        if variant is None:
            variant = Variant(
                shop_id=shop.id,
                product_id=product.id,
                shopify_id=int(raw["id"]),
                inventory_quantity=int(raw.get("inventory_quantity") or 0),
            )
            db.add(variant)
        variant.sku = raw.get("sku")
        variant.title = str(raw.get("title") or "Default")
        variant.price_minor = to_minor(raw.get("price"))
        variant.inventory_item_id = raw.get("inventory_item_id")
    await db.flush()
    return NormalizeResult(
        changes=[Change("product.updated", {"id": str(product.id), "title": product.title})]
    )


async def handle_inventory_level(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    row = (
        await db.execute(
            select(Variant, Product)
            .join(Product, Product.id == Variant.product_id)
            .where(
                Variant.shop_id == shop.id,
                Variant.inventory_item_id == int(p["inventory_item_id"]),
            )
        )
    ).one_or_none()
    if row is None:
        return NormalizeResult(stale=True)
    variant, product = row
    available = int(p.get("available") or 0)
    delta = available - variant.inventory_quantity
    variant.inventory_quantity = available
    db.add(
        InventorySnapshot(
            shop_id=shop.id,
            variant_id=variant.id,
            quantity=available,
            delta=delta,
            reason=str(p.get("reason") or "inventory_levels/update")[:64],
            recorded_at=parse_ts(p.get("updated_at")) or utcnow(),
        )
    )
    await db.flush()
    return NormalizeResult(
        changes=[
            Change(
                "inventory.changed",
                {
                    "variant_id": str(variant.id),
                    "product": product.title,
                    "variant": variant.title,
                    "sku": variant.sku,
                    "quantity": available,
                    "delta": delta,
                    "reorder_point": variant.reorder_point,
                    "low": variant.reorder_point is not None and available <= variant.reorder_point,
                },
            )
        ]
    )


async def handle_checkout(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    token = str(p["token"])
    updated_at = parse_ts(p.get("updated_at"))
    checkout = await db.scalar(
        select(Checkout).where(Checkout.shop_id == shop.id, Checkout.token == token)
    )
    if checkout is not None and _is_stale(checkout.shopify_updated_at, updated_at):
        return NormalizeResult(stale=True)
    customer = await _upsert_customer(db, shop, p.get("customer"))
    created = checkout is None
    if checkout is None:
        checkout = Checkout(
            shop_id=shop.id,
            token=token,
            shopify_created_at=parse_ts(p.get("created_at")) or utcnow(),
        )
        db.add(checkout)
    checkout.shopify_id = p.get("id")
    checkout.customer_id = customer.id if customer else checkout.customer_id
    checkout.email = p.get("email") or checkout.email
    checkout.currency = str(p.get("currency") or shop.currency)
    checkout.total_minor = to_minor(p.get("total_price"))
    checkout.recovery_url = p.get("abandoned_checkout_url")
    checkout.line_items = [
        {
            "title": li.get("title"),
            "variant_id": li.get("variant_id"),
            "sku": li.get("sku"),
            "quantity": li.get("quantity"),
            "price_minor": to_minor(li.get("price")),
        }
        for li in p.get("line_items") or []
    ]
    checkout.shopify_updated_at = updated_at
    if p.get("completed_at") and checkout.status in (CheckoutStatus.OPEN, CheckoutStatus.ABANDONED):
        checkout.status = CheckoutStatus.COMPLETED
        checkout.completed_at = parse_ts(p["completed_at"])
    await db.flush()
    return NormalizeResult(
        changes=[
            Change(
                "checkout.created" if created else "checkout.updated",
                {
                    "id": str(checkout.id),
                    "token": checkout.token,
                    "status": checkout.status.value,
                    "total_minor": checkout.total_minor,
                    "currency": checkout.currency,
                    "item_count": sum(int(li.get("quantity") or 1) for li in checkout.line_items),
                    "customer_name": customer.first_name if customer else None,
                },
            )
        ]
    )


async def handle_review(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    source = ReviewSource(p.get("source", ReviewSource.SHOPIFY.value))
    external_id = str(p["id"])
    review = await db.scalar(
        select(Review).where(
            Review.shop_id == shop.id, Review.source == source, Review.external_id == external_id
        )
    )
    if review is not None:
        return NormalizeResult(stale=True)
    product = None
    if p.get("product_id"):
        product = await db.scalar(
            select(Product).where(
                Product.shop_id == shop.id, Product.shopify_id == int(p["product_id"])
            )
        )
    review = Review(
        shop_id=shop.id,
        source=source,
        external_id=external_id,
        product_id=product.id if product else None,
        rating=max(1, min(5, int(p["rating"]))),
        title=p.get("title"),
        body=str(p.get("body") or ""),
        author_name=p.get("author"),
        published_at=parse_ts(p.get("created_at")) or utcnow(),
    )
    db.add(review)
    await db.flush()
    return NormalizeResult(
        changes=[
            Change(
                "review.created",
                {
                    "id": str(review.id),
                    "rating": review.rating,
                    "title": review.title,
                    "body": review.body,
                    "author": review.author_name,
                    "product": product.title if product else None,
                },
            )
        ]
    )


async def handle_support_message(db: AsyncSession, shop: Shop, p: Payload) -> NormalizeResult:
    email = str(p.get("from_email") or "")
    order = None
    if p.get("order_name"):
        order = await db.scalar(
            select(Order).where(Order.shop_id == shop.id, Order.name == p["order_name"])
        )
    received_at = parse_ts(p.get("received_at")) or utcnow()
    ticket = Ticket(
        shop_id=shop.id,
        channel=TicketChannel(p.get("channel", TicketChannel.EMAIL.value)),
        subject=str(p.get("subject") or "(no subject)")[:255],
        customer_email=email or None,
        customer_id=order.customer_id if order else None,
        order_id=order.id if order else None,
        last_message_at=received_at,
    )
    db.add(ticket)
    await db.flush()
    db.add(
        Message(
            shop_id=shop.id,
            ticket_id=ticket.id,
            direction=MessageDirection.INBOUND,
            author_type=MessageAuthor.CUSTOMER,
            body=str(p.get("body") or ""),
            external_id=str(p.get("id")) if p.get("id") else None,
            sent_at=received_at,
        )
    )
    await db.flush()
    return NormalizeResult(
        changes=[
            Change(
                "ticket.created",
                {
                    "id": str(ticket.id),
                    "subject": ticket.subject,
                    "customer_name": p.get("first_name"),
                    "order_name": order.name if order else p.get("order_name"),
                    "channel": ticket.channel.value,
                },
            )
        ]
    )


HANDLERS: dict[str, Handler] = {
    "orders/create": handle_order,
    "orders/updated": handle_order,
    "orders/cancelled": handle_order_cancelled,
    "refunds/create": handle_refund,
    "fulfillments/create": handle_fulfillment,
    "products/update": handle_product,
    "inventory_levels/update": handle_inventory_level,
    "checkouts/create": handle_checkout,
    "checkouts/update": handle_checkout,
    # Not Shopify webhook topics: reviews arrive from review integrations and support messages
    # from the inbox / chat widget. They share the same pipeline.
    "reviews/create": handle_review,
    "support/message": handle_support_message,
}


async def normalize(db: AsyncSession, shop: Shop, topic: str, payload: Payload) -> NormalizeResult:
    handler = HANDLERS.get(topic)
    if handler is None:
        return NormalizeResult()
    return await handler(db, shop, payload)
