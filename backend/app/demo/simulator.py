"""Northbound Outdoor Gear store simulator.

Produces Shopify-shaped webhook payloads and feeds them through the same ingest path as real
webhooks (`source=simulator`). Nothing leaves the process: no Shopify calls, no email.
"""

import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.orchestrator import Activity, publish_activity
from app.core.queue import JobQueue
from app.demo.catalog import (
    ANGRY_REVIEW,
    COUNTRIES,
    FIRST_NAMES,
    HIGH_RISK_COUNTRIES,
    NEGATIVE_REVIEWS,
    NEUTRAL_REVIEWS,
    POSITIVE_REVIEWS,
    SHIPPING_DELAY_TEMPLATE,
    SOURCES,
    SUPPORT_TEMPLATES,
)
from app.demo.seed import DEMO_ID_BASE, weighted
from app.models import Customer, Order, Product, Shop, Variant
from app.models.base import utcnow
from app.models.enums import EventSource, ShopMode, ShopStatus
from app.pipeline.ingest import ingest_event

WATCHED_KEY = "demo:watched"
INITIALS = "ABCDEFGHJKLMNPRSTW"
WATCH_TTL_SECONDS = 180

SCENARIOS: dict[str, str] = {
    "flash_sale": "Flash sale spike",
    "fraud_attempt": "Fraud attempt",
    "stockout": "Stockout",
    "angry_review": "Angry review",
    "shipping_delay": "Shipping delay",
}


def _key(shop_id: uuid.UUID, name: str) -> str:
    return f"demo:{shop_id}:{name}"


async def mark_watched(redis: Redis, shop_id: uuid.UUID) -> None:
    """Simulate traffic only for demo shops someone is looking at."""
    await redis.zadd(WATCHED_KEY, {str(shop_id): time.time() + WATCH_TTL_SECONDS})


async def watched_shops(redis: Redis) -> list[uuid.UUID]:
    now = time.time()
    await redis.zremrangebyscore(WATCHED_KEY, "-inf", now)
    return [uuid.UUID(member) for member in await redis.zrangebyscore(WATCHED_KEY, now, "+inf")]


async def is_paused(redis: Redis, shop_id: uuid.UUID) -> bool:
    return bool(await redis.exists(_key(shop_id, "paused")))


async def set_paused(redis: Redis, shop_id: uuid.UUID, paused: bool) -> None:
    if paused:
        await redis.set(_key(shop_id, "paused"), "1")
    else:
        await redis.delete(_key(shop_id, "paused"))


@dataclass(slots=True)
class StockItem:
    product_shopify_id: int
    product_title: str
    variant_shopify_id: int
    inventory_item_id: int
    sku: str
    title: str
    price_minor: int
    quantity: int
    popularity: float


@dataclass(slots=True)
class Line:
    item: StockItem
    quantity: int


def _money(minor: int) -> str:
    return f"{minor / 100:.2f}"


class DemoStore:
    """One simulation step's view of a demo shop."""

    def __init__(
        self,
        shop: Shop,
        db: AsyncSession,
        redis: Redis,
        queue: JobQueue,
        rng: random.Random | None = None,
        now: datetime | None = None,
    ) -> None:
        self.shop = shop
        self.db = db
        self.redis = redis
        self.queue = queue
        self.rng = rng or random.Random()
        self.now = now or utcnow()
        self.stock: list[StockItem] = []

    async def load(self) -> "DemoStore":
        rows = (
            await self.db.execute(
                select(Variant, Product)
                .join(Product, Product.id == Variant.product_id)
                .where(Variant.shop_id == self.shop.id)
                .order_by(Variant.sku)
            )
        ).all()
        self.stock = [
            StockItem(
                product_shopify_id=product.shopify_id,
                product_title=product.title,
                variant_shopify_id=variant.shopify_id,
                inventory_item_id=variant.inventory_item_id or 0,
                sku=variant.sku or "",
                title=variant.title,
                price_minor=variant.price_minor,
                quantity=variant.inventory_quantity,
                # Cheaper items sell more often.
                popularity=max(0.2, 30000 / max(variant.price_minor, 1000)),
            )
            for variant, product in rows
        ]
        return self

    # ---- ids ------------------------------------------------------------------------------

    async def _next_id(self) -> int:
        key = _key(self.shop.id, "id_seq")
        await self.redis.set(key, DEMO_ID_BASE + 5_000_000, nx=True)
        return int(await self.redis.incr(key))

    async def _next_order_name(self) -> str:
        key = _key(self.shop.id, "order_seq")
        if not await self.redis.exists(key):
            count = await self.db.scalar(
                select(func.count(Order.id)).where(Order.shop_id == self.shop.id)
            )
            await self.redis.set(key, 1000 + int(count or 0), nx=True)
        return f"#{int(await self.redis.incr(key))}"

    # ---- emission -------------------------------------------------------------------------

    async def emit(self, topic: str, payload: dict[str, Any]) -> None:
        await ingest_event(
            self.db,
            self.queue,
            shop_id=self.shop.id,
            topic=topic,
            webhook_id=f"sim-{uuid.uuid4()}",
            payload=payload,
            source=EventSource.SIMULATOR,
            triggered_at=utcnow(),
        )

    # ---- builders -------------------------------------------------------------------------

    def pick_lines(self, max_lines: int = 3, focus: list[StockItem] | None = None) -> list[Line]:
        pool = [item for item in (focus or self.stock) if item.quantity > 0]
        if not pool:
            return []
        count = min(len(pool), self.rng.choices((1, 2, 3), (0.62, 0.28, 0.1))[0], max_lines)
        chosen: list[StockItem] = []
        while len(chosen) < count:
            item = self.rng.choices(pool, weights=[i.popularity for i in pool])[0]
            if item not in chosen:
                chosen.append(item)
        return [Line(item, 1 if self.rng.random() < 0.85 else 2) for item in chosen]

    async def random_customer(self, new: bool = False) -> dict[str, Any]:
        if not new and self.rng.random() < 0.6:
            customer = await self.db.scalar(
                select(Customer)
                .where(Customer.shop_id == self.shop.id)
                .offset(self.rng.randint(0, 250))
                .limit(1)
            )
            if customer is not None:
                return {
                    "id": customer.shopify_id,
                    "email": customer.email,
                    "first_name": customer.first_name,
                    "orders_count": customer.orders_count + 1,
                    "total_spent": _money(customer.total_spent_minor),
                    "accepts_marketing": customer.accepts_marketing,
                    "default_address": {"country_code": customer.country_code},
                }
        name = self.rng.choice(FIRST_NAMES)
        return {
            "id": await self._next_id(),
            "email": f"{name.lower()}.{self.rng.randint(100, 99999)}@shoppers.example",
            "first_name": name,
            "orders_count": 1,
            "total_spent": "0.00",
            "accepts_marketing": self.rng.random() < 0.5,
            "default_address": {"country_code": weighted(self.rng, COUNTRIES)},
        }

    def _line_items(self, lines: list[Line]) -> list[dict[str, Any]]:
        return [
            {
                "variant_id": line.item.variant_shopify_id,
                "product_id": line.item.product_shopify_id,
                "title": f"{line.item.product_title} - {line.item.title}",
                "sku": line.item.sku,
                "quantity": line.quantity,
                "price": _money(line.item.price_minor),
            }
            for line in lines
        ]

    def checkout_payload(
        self, checkout_id: int, token: str, customer: dict[str, Any], lines: list[Line]
    ) -> dict[str, Any]:
        total = sum(line.item.price_minor * line.quantity for line in lines)
        stamp = utcnow().isoformat()
        return {
            "id": checkout_id,
            "token": token,
            "email": customer["email"],
            "currency": self.shop.currency,
            "created_at": stamp,
            "updated_at": stamp,
            "completed_at": None,
            "total_price": _money(total),
            "abandoned_checkout_url": f"https://{self.shop.domain}/checkouts/{token}/recover",
            "customer": customer,
            "line_items": self._line_items(lines),
        }

    async def place_order(
        self,
        customer: dict[str, Any],
        lines: list[Line],
        *,
        checkout_token: str | None = None,
        shipping_country: str | None = None,
        billing_country: str | None = None,
        discount_pct: int = 0,
        financial_status: str = "paid",
        source: str | None = None,
    ) -> str:
        """Emit orders/create plus the inventory updates Shopify would send."""
        subtotal = sum(line.item.price_minor * line.quantity for line in lines)
        discount = subtotal * discount_pct // 100
        name = await self._next_order_name()
        stamp = utcnow().isoformat()
        country = customer.get("default_address", {}).get("country_code") or "US"
        order = {
            "id": await self._next_id(),
            "name": name,
            "email": customer["email"],
            "created_at": stamp,
            "processed_at": stamp,
            "updated_at": stamp,
            "currency": self.shop.currency,
            "subtotal_price": _money(subtotal),
            "total_discounts": _money(discount),
            "total_price": _money(subtotal - discount),
            "financial_status": financial_status,
            "fulfillment_status": None,
            "source_name": source or weighted(self.rng, SOURCES),
            "checkout_token": checkout_token,
            "customer": customer,
            "shipping_address": {"country_code": shipping_country or country},
            "billing_address": {"country_code": billing_country or country},
            "discount_codes": [{"code": "FLASH20"}] if discount_pct else [],
            "line_items": [{"id": await self._next_id(), **li} for li in self._line_items(lines)],
        }
        await self.emit("orders/create", order)
        for line in lines:
            line.item.quantity = max(0, line.item.quantity - line.quantity)
            await self.emit(
                "inventory_levels/update",
                {
                    "inventory_item_id": line.item.inventory_item_id,
                    "available": line.item.quantity,
                    "updated_at": utcnow().isoformat(),
                    "reason": "order",
                },
            )
        return name

    async def start_checkout(self, converts: bool, delay: float) -> None:
        lines = self.pick_lines()
        if not lines:
            return
        customer = await self.random_customer()
        token = uuid.uuid4().hex
        payload = self.checkout_payload(await self._next_id(), token, customer, lines)
        await self.emit("checkouts/create", payload)
        if converts:
            pending = {
                "due": time.time() + delay,
                "checkout": payload,
                "lines": [[line.item.sku, line.quantity] for line in lines],
            }
            await self.redis.hset(_key(self.shop.id, "pending"), token, json.dumps(pending))  # type: ignore[misc]

    async def complete_due_checkouts(self) -> int:
        key = _key(self.shop.id, "pending")
        pending: dict[str, str] = await self.redis.hgetall(key)  # type: ignore[misc]
        by_sku = {item.sku: item for item in self.stock}
        completed = 0
        for token, raw in pending.items():
            entry = json.loads(raw)
            if entry["due"] > time.time():
                continue
            await self.redis.hdel(key, token)  # type: ignore[misc]
            lines = [
                Line(by_sku[sku], qty)
                for sku, qty in entry["lines"]
                if sku in by_sku and by_sku[sku].quantity >= qty
            ]
            if not lines:
                continue
            checkout = entry["checkout"]
            stamp = utcnow().isoformat()
            await self.emit(
                "checkouts/update", {**checkout, "updated_at": stamp, "completed_at": stamp}
            )
            await self.place_order(checkout["customer"], lines, checkout_token=token)
            completed += 1
        return completed

    async def review(self, rating: int | None = None, text: tuple[str, str] | None = None) -> None:
        rating = rating or self.rng.choices((5, 4, 3, 2, 1), (0.52, 0.26, 0.1, 0.07, 0.05))[0]
        if text is None:
            pool = (
                POSITIVE_REVIEWS
                if rating >= 4
                else NEUTRAL_REVIEWS
                if rating == 3
                else NEGATIVE_REVIEWS
            )
            text = self.rng.choice(pool)
        item = self.rng.choice(self.stock) if self.stock else None
        await self.emit(
            "reviews/create",
            {
                "id": f"rev-{uuid.uuid4().hex[:12]}",
                "source": "simulator",
                "product_id": item.product_shopify_id if item else None,
                "rating": rating,
                "title": text[0],
                "body": text[1],
                "author": f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(INITIALS)}.",
                "created_at": utcnow().isoformat(),
            },
        )

    async def recent_order_names(self, limit: int = 6) -> list[str]:
        rows = await self.db.scalars(
            select(Order.name)
            .where(Order.shop_id == self.shop.id, Order.fulfillment_status.is_(None))
            .order_by(Order.processed_at.desc())
            .limit(limit)
        )
        return list(rows.all())

    async def support_email(self, template: tuple[str, str] | None = None) -> None:
        names = await self.recent_order_names()
        order_name = self.rng.choice(names) if names else "#1001"
        subject, body = template or self.rng.choice(SUPPORT_TEMPLATES)
        first = self.rng.choice(FIRST_NAMES)
        uses_order = "{order}" in subject + body
        await self.emit(
            "support/message",
            {
                "id": f"msg-{uuid.uuid4().hex[:12]}",
                "channel": "email",
                "from_email": f"{first.lower()}@customers.example",
                "first_name": first,
                "subject": subject.format(order=order_name),
                "body": body.format(order=order_name),
                "order_name": order_name if uses_order else None,
                "received_at": utcnow().isoformat(),
            },
        )

    async def maybe_restock(self) -> None:
        for item in self.stock:
            if item.quantity <= 3 and self.rng.random() < 0.03:
                item.quantity += self.rng.choice((24, 36, 48))
                await self.emit(
                    "inventory_levels/update",
                    {
                        "inventory_item_id": item.inventory_item_id,
                        "available": item.quantity,
                        "updated_at": utcnow().isoformat(),
                        "reason": "restock",
                    },
                )

    async def fulfil_one(self) -> None:
        order = await self.db.scalar(
            select(Order)
            .where(
                Order.shop_id == self.shop.id,
                Order.fulfillment_status.is_(None),
                Order.cancelled_at.is_(None),
                Order.processed_at < utcnow() - timedelta(minutes=2),
            )
            .order_by(Order.processed_at)
            .limit(1)
        )
        if order is None:
            return
        await self.emit(
            "fulfillments/create",
            {
                "id": await self._next_id(),
                "order_id": order.shopify_id,
                "status": "success",
                "tracking_number": f"1Z{self.rng.randint(10**9, 10**10 - 1)}",
                "created_at": utcnow().isoformat(),
            },
        )

    # ---- one simulation tick --------------------------------------------------------------

    async def tick(self) -> None:
        await self.complete_due_checkouts()
        if self.rng.random() < 0.75:
            await self.start_checkout(
                converts=self.rng.random() < 0.55, delay=self.rng.uniform(3, 12)
            )
        if self.rng.random() < 0.015:
            await self.fraudulent_order()
        if self.rng.random() < 0.06:
            await self.review()
        if self.rng.random() < 0.05:
            await self.support_email()
        if self.rng.random() < 0.12:
            await self.fulfil_one()
        await self.maybe_restock()

    async def fraudulent_order(self) -> str | None:
        # Unusual quantity of a high-value item, new customer, billed abroad, payment pending.
        expensive = [
            item
            for item in sorted(self.stock, key=lambda i: i.price_minor, reverse=True)[:6]
            if item.quantity >= 4
        ]
        lines = [Line(self.rng.choice(expensive), self.rng.randint(3, 4))] if expensive else []
        if not lines:
            return None
        customer = await self.random_customer(new=True)
        customer["email"] = f"{uuid.uuid4().hex[:10]}@quickmail.example"
        return await self.place_order(
            customer,
            lines,
            shipping_country="US",
            billing_country=self.rng.choice(HIGH_RISK_COUNTRIES),
            financial_status="pending",
            source="web",
        )

    # ---- scenarios ------------------------------------------------------------------------

    async def run_scenario(self, name: str, pause: float = 0.6) -> None:
        if name not in SCENARIOS:
            raise ValueError(f"unknown scenario {name}")
        await publish_activity(
            self.redis,
            self.shop.id,
            Activity(f"Scenario started: {SCENARIOS[name]}", "Triggered from the demo menu."),
            kind="scenario.started",
            ref={"scenario": name},
        )
        await getattr(self, f"_scenario_{name}")(pause)
        await publish_activity(
            self.redis,
            self.shop.id,
            Activity(f"Scenario finished: {SCENARIOS[name]}", "Back to normal traffic."),
            kind="scenario.completed",
            ref={"scenario": name},
        )

    async def _scenario_flash_sale(self, pause: float) -> None:
        hero = sorted(self.stock, key=lambda i: i.popularity * i.quantity, reverse=True)[:3]
        for _ in range(self.rng.randint(16, 22)):
            lines = self.pick_lines(max_lines=2, focus=hero)
            if not lines:
                break
            customer = await self.random_customer()
            token = uuid.uuid4().hex
            await self.emit(
                "checkouts/create",
                self.checkout_payload(await self._next_id(), token, customer, lines),
            )
            await self.place_order(
                customer, lines, checkout_token=token, discount_pct=20, source="instagram"
            )
            await asyncio.sleep(pause * self.rng.uniform(0.3, 1.0))

    async def _scenario_fraud_attempt(self, pause: float) -> None:
        for _ in range(3):
            await self.fraudulent_order()
            await asyncio.sleep(pause * 2)

    async def _scenario_stockout(self, pause: float) -> None:
        candidates = [i for i in self.stock if i.quantity > 0]
        if not candidates:
            return
        target = max(candidates, key=lambda i: i.popularity)
        while target.quantity > 0:
            qty = min(target.quantity, self.rng.randint(1, 3))
            await self.place_order(await self.random_customer(), [Line(target, qty)])
            await asyncio.sleep(pause)
            if target.quantity > 6:
                # Burn down large stock quickly: a wholesale order takes most of it.
                big = target.quantity - self.rng.randint(2, 4)
                await self.place_order(await self.random_customer(), [Line(target, big)])

    async def _scenario_angry_review(self, pause: float) -> None:
        await self.review(rating=1, text=ANGRY_REVIEW)
        await asyncio.sleep(pause * 2)
        await self.support_email(
            ("Broken zipper — refund now", ANGRY_REVIEW[1] + " Order {order}.")
        )

    async def _scenario_shipping_delay(self, pause: float) -> None:
        for _ in range(4):
            await self.support_email(SHIPPING_DELAY_TEMPLATE)
            await asyncio.sleep(pause * 1.5)


async def simulate_tick(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Redis, queue: JobQueue
) -> int:
    """One step for every watched, running demo shop. Returns how many shops were simulated."""
    simulated = 0
    for shop_id in await watched_shops(redis):
        if await is_paused(redis, shop_id):
            continue
        async with sessionmaker() as db:
            shop = await db.get(Shop, shop_id)
            if shop is None or shop.mode != ShopMode.DEMO or shop.status != ShopStatus.ACTIVE:
                continue
            store = await DemoStore(shop, db, redis, queue).load()
            await store.tick()
            simulated += 1
    return simulated


async def run_scenario(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    queue: JobQueue,
    shop_id: uuid.UUID,
    name: str,
    pause: float = 0.6,
) -> None:
    async with sessionmaker() as db:
        shop = await db.get(Shop, shop_id)
        if shop is None or shop.mode != ShopMode.DEMO:
            return
        store = await DemoStore(shop, db, redis, queue).load()
        try:
            await store.run_scenario(name, pause=pause)
        except Exception:
            await publish_activity(
                redis,
                shop_id,
                Activity(f"Scenario failed: {SCENARIOS.get(name, name)}", "See worker logs."),
                kind="scenario.failed",
            )
            raise
