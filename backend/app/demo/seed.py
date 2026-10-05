"""Seed a demo shop with the Northbound catalog, customers and five weeks of order history, so
KPIs, forecasts and week-over-week comparisons are meaningful from the first minute."""

import random
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.catalog import CATALOG, COUNTRIES, FIRST_NAMES, SOURCES
from app.models import (
    AgentConfig,
    Checkout,
    Customer,
    Order,
    OrderItem,
    Product,
    Shop,
    ShopSettings,
    Variant,
)
from app.models.base import utcnow
from app.models.enums import AgentName, Autonomy, CheckoutStatus

HISTORY_DAYS = 35
BASE_ORDERS_PER_DAY = 28
# Index 0 = Monday. Outdoor gear sells best heading into the weekend.
WEEKDAY_FACTOR = (0.85, 0.85, 0.9, 1.0, 1.15, 1.3, 1.2)
# Relative order volume per local hour.
HOUR_WEIGHTS = (
    0.2, 0.15, 0.1, 0.08, 0.08, 0.15, 0.35, 0.6, 0.8, 0.95, 1.0, 1.05,
    1.15, 1.1, 1.0, 0.95, 1.0, 1.1, 1.3, 1.5, 1.6, 1.4, 0.9, 0.45,
)  # fmt: skip

DEMO_ID_BASE = 7_000_000_000


def weighted(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


async def configure_demo_shop(db: AsyncSession, shop: Shop) -> None:
    """Demo-friendly settings: compressed timings so agents act within a minute or two."""
    settings = (
        await db.execute(select(ShopSettings).where(ShopSettings.shop_id == shop.id))
    ).scalar_one()
    settings.sender_name = "Northbound Outdoor Gear"
    settings.support_email = "help@northbound.example"
    settings.alert_email = "ops@northbound.example"
    settings.physical_address = "1200 Trailhead Way, Boulder, CO 80302, USA"
    configs = {
        c.agent: c
        for c in (await db.scalars(select(AgentConfig).where(AgentConfig.shop_id == shop.id))).all()
    }
    configs[AgentName.CART_RECOVERY].autonomy = Autonomy.AUTO
    configs[AgentName.CART_RECOVERY].settings = {
        "abandon_after_minutes": 2,
        "reminder_interval_minutes": 5,
        "discount_pct": 10,
    }
    configs[AgentName.INVENTORY_PLANNER].settings = {
        "supplier_email": "purchasing@northbound-supply.example"
    }
    await db.flush()


async def seed_demo_shop(db: AsyncSession, shop: Shop, now: datetime | None = None) -> None:
    now = now or utcnow()
    await configure_demo_shop(db, shop)
    rng = random.Random(shop.id.int)
    next_id = DEMO_ID_BASE

    def new_shopify_id() -> int:
        nonlocal next_id
        next_id += 1
        return next_id

    # Catalog
    variants: list[dict[str, Any]] = []
    product_rows: list[dict[str, Any]] = []
    for product in CATALOG:
        product_id = uuid.uuid4()
        product_rows.append(
            {
                "id": product_id,
                "shop_id": shop.id,
                "shopify_id": new_shopify_id(),
                "title": product.title,
                "handle": product.handle,
                "vendor": "Northbound",
                "product_type": product.product_type,
                "status": "active",
                "shopify_updated_at": now,
            }
        )
        for v in product.variants:
            variants.append(
                {
                    "id": uuid.uuid4(),
                    "shop_id": shop.id,
                    "product_id": product_id,
                    "shopify_id": new_shopify_id(),
                    "inventory_item_id": new_shopify_id(),
                    "sku": v.sku,
                    "title": v.title,
                    "price_minor": v.price_minor,
                    "cost_minor": v.cost_minor,
                    "inventory_quantity": v.stock,
                    "reorder_point": v.reorder_point,
                    "lead_time_days": 10,
                    "_popularity": v.popularity,
                    "_product_title": product.title,
                }
            )
    await db.execute(insert(Product), product_rows)
    await db.execute(
        insert(Variant),
        [{k: v for k, v in row.items() if not k.startswith("_")} for row in variants],
    )

    # Customers
    customers: list[dict[str, Any]] = []
    for i in range(320):
        name = rng.choice(FIRST_NAMES)
        customers.append(
            {
                "id": uuid.uuid4(),
                "shop_id": shop.id,
                "shopify_id": new_shopify_id(),
                "first_name": name,
                "email": f"{name.lower()}.{i}@customers.example",
                "country_code": weighted(rng, COUNTRIES),
                "accepts_marketing": rng.random() < 0.55,
                "orders_count": 0,
                "total_spent_minor": 0,
            }
        )

    # Orders and checkouts
    tz = ZoneInfo(shop.timezone)
    today = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    popularity = [v["_popularity"] for v in variants]
    orders: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    checkouts: list[dict[str, Any]] = []
    order_number = 1000

    for day_offset in range(HISTORY_DAYS, -1, -1):
        day = today - timedelta(days=day_offset)
        volume = BASE_ORDERS_PER_DAY * WEEKDAY_FACTOR[day.weekday()] * rng.uniform(0.85, 1.15)
        # Slow growth over the period so week-over-week trends are visible.
        volume *= 1 + (HISTORY_DAYS - day_offset) * 0.004
        for _ in range(round(volume)):
            hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
            placed = day + timedelta(
                hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
            )
            if placed >= now:
                continue
            customer = rng.choice(customers)
            customer["orders_count"] += 1
            order_id = uuid.uuid4()
            order_number += 1
            lines = rng.choices(
                variants, weights=popularity, k=rng.choices((1, 2, 3), (0.6, 0.3, 0.1))[0]
            )
            subtotal = 0
            for variant in lines:
                qty = 1 if rng.random() < 0.85 else 2
                subtotal += variant["price_minor"] * qty
                items.append(
                    {
                        "id": uuid.uuid4(),
                        "shop_id": shop.id,
                        "order_id": order_id,
                        "variant_id": variant["id"],
                        "title": f"{variant['_product_title']} - {variant['title']}",
                        "sku": variant["sku"],
                        "quantity": qty,
                        "price_minor": variant["price_minor"],
                    }
                )
            discount = subtotal // 10 if rng.random() < 0.12 else 0
            total = subtotal - discount
            customer["total_spent_minor"] += total
            refunded = total if rng.random() < 0.02 else 0
            token = uuid.uuid4().hex
            orders.append(
                {
                    "id": order_id,
                    "shop_id": shop.id,
                    "shopify_id": new_shopify_id(),
                    "name": f"#{order_number}",
                    "customer_id": customer["id"],
                    "email": customer["email"],
                    "checkout_token": token,
                    "currency": shop.currency,
                    "subtotal_minor": subtotal,
                    "discount_minor": discount,
                    "total_minor": total,
                    "refunded_minor": refunded,
                    "financial_status": "refunded" if refunded else "paid",
                    "fulfillment_status": "fulfilled" if day_offset > 1 else None,
                    "shipping_country": customer["country_code"],
                    "billing_country": customer["country_code"],
                    "source_name": weighted(rng, SOURCES),
                    "risk_score": None,
                    "processed_at": placed,
                    "shopify_updated_at": placed,
                }
            )
            # Some past orders came back through a recovery email.
            recovered = customer["accepts_marketing"] and rng.random() < 0.07
            completed = _checkout(
                shop,
                token,
                customer,
                total,
                placed - timedelta(hours=3) if recovered else placed,
                CheckoutStatus.RECOVERED if recovered else CheckoutStatus.COMPLETED,
                placed,
                lines,
            )
            if recovered:
                completed["reminders_sent"] = rng.choice((1, 1, 2))
                completed["last_reminder_at"] = placed - timedelta(minutes=rng.randint(10, 90))
                completed["recovered_order_id"] = order_id
            checkouts.append(completed)
            # Roughly one abandoned checkout for every completed one.
            if rng.random() < 0.9:
                abandoned_at = placed - timedelta(minutes=rng.randint(5, 600))
                if abandoned_at < now - timedelta(hours=1):
                    shopper = rng.choice(customers)
                    item = rng.choice(variants)
                    cart = _checkout(
                        shop,
                        uuid.uuid4().hex,
                        shopper,
                        item["price_minor"],
                        abandoned_at,
                        CheckoutStatus.ABANDONED,
                        None,
                        [item],
                    )
                    if shopper["accepts_marketing"] and day_offset > 0:
                        # Past follow-ups are complete, so the live agent won't re-contact them.
                        cart["reminders_sent"] = 2
                        cart["last_reminder_at"] = abandoned_at + timedelta(hours=1)
                    checkouts.append(cart)

    await db.execute(insert(Customer), customers)
    for chunk in range(0, len(orders), 500):
        await db.execute(insert(Order), orders[chunk : chunk + 500])
    for chunk in range(0, len(items), 1000):
        await db.execute(insert(OrderItem), items[chunk : chunk + 1000])
    for chunk in range(0, len(checkouts), 1000):
        await db.execute(insert(Checkout), checkouts[chunk : chunk + 1000])
    await db.flush()


def _checkout(
    shop: Shop,
    token: str,
    customer: dict[str, Any],
    total: int,
    created: datetime,
    status: CheckoutStatus,
    completed: datetime | None,
    variants: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "shop_id": shop.id,
        "token": token,
        "customer_id": customer["id"],
        "email": customer["email"],
        "currency": shop.currency,
        "total_minor": total,
        "line_items": [
            {
                "title": f"{v['_product_title']} - {v['title']}",
                "variant_id": v["shopify_id"],
                "sku": v["sku"],
                "quantity": 1,
                "price_minor": v["price_minor"],
            }
            for v in variants
        ],
        "status": status,
        "reminders_sent": 0,
        "last_reminder_at": None,
        "recovered_order_id": None,
        "completed_at": completed,
        "shopify_created_at": created,
        "shopify_updated_at": completed or created,
    }
