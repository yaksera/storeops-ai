import random
import uuid

import fakeredis
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.demo import simulator
from app.demo.catalog import CATALOG, HIGH_RISK_COUNTRIES
from app.demo.simulator import DemoStore
from app.models import Checkout, Customer, Order, Product, Review, Shop, Ticket, Variant
from app.models.enums import CheckoutStatus, ShopMode
from app.services import events
from app.services.shops import create_shop
from tests.conftest import InlineQueue, Session, signup


async def test_demo_shop_is_seeded_with_catalog_and_history(
    db: AsyncSession, demo_shop: dict[str, str]
) -> None:
    shop_id = uuid.UUID(demo_shop["id"])
    products = await db.scalar(
        select(func.count()).select_from(Product).where(Product.shop_id == shop_id)
    )
    variants = await db.scalar(
        select(func.count()).select_from(Variant).where(Variant.shop_id == shop_id)
    )
    orders = await db.scalar(
        select(func.count()).select_from(Order).where(Order.shop_id == shop_id)
    )
    customers = await db.scalar(
        select(func.count()).select_from(Customer).where(Customer.shop_id == shop_id)
    )
    abandoned = await db.scalar(
        select(func.count())
        .select_from(Checkout)
        .where(Checkout.shop_id == shop_id, Checkout.status == CheckoutStatus.ABANDONED)
    )
    assert products == len(CATALOG)
    assert variants == sum(len(p.variants) for p in CATALOG)
    assert orders is not None
    assert orders > 30 * 20
    assert customers == 320
    assert abandoned is not None
    assert abandoned > 100


async def test_dashboard_snapshot(owner: Session, demo_shop: dict[str, str]) -> None:
    response = await owner.get(f"/api/shops/{demo_shop['id']}/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert set(body["kpis"]) >= {"revenue_minor", "orders", "aov_minor", "conversion_rate"}
    assert body["kpis"]["revenue_last_week_minor"] >= 0
    assert len(body["revenue_by_hour"]["today"]) == 24
    assert len(body["orders"]) == 25
    assert [a["agent"] for a in body["agents"]][:2] == ["orchestrator", "fraud_guard"]
    assert all(a["status"] == "watching" for a in body["agents"])
    assert body["simulator"]["running"] is True
    assert {s["id"] for s in body["simulator"]["scenarios"]} == set(simulator.SCENARIOS)
    assert body["last_event_id"] == "0-0"


async def _store(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, shop_id: str
) -> DemoStore:
    shop = await db.get(Shop, uuid.UUID(shop_id))
    assert shop is not None
    return await DemoStore(shop, db, redis, queue, rng=random.Random(7)).load()


async def test_checkout_conversion_creates_order_and_decrements_stock(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    store = await _store(db, redis, queue, demo_shop["id"])
    before = await db.scalar(select(func.count()).select_from(Order))
    await store.start_checkout(converts=True, delay=0)
    assert await store.complete_due_checkouts() == 1
    after = await db.scalar(select(func.count()).select_from(Order))
    assert after == (before or 0) + 1
    newest = await db.scalar(select(Order).order_by(Order.created_at.desc()).limit(1))
    assert newest is not None
    checkout = await db.scalar(select(Checkout).where(Checkout.token == newest.checkout_token))
    assert checkout is not None
    assert checkout.status == CheckoutStatus.COMPLETED


async def test_ticks_generate_traffic(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    store = await _store(db, redis, queue, demo_shop["id"])
    for _ in range(25):
        await store.tick()
    topics = {job[0] for job in queue.jobs}
    assert topics == {"process_webhook_event"}
    assert len(queue.jobs) > 10


async def test_simulate_tick_only_runs_for_watched_unpaused_shops(
    redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop_id = uuid.UUID(demo_shop["id"])
    assert await simulator.simulate_tick(get_sessionmaker(), redis, queue) == 0
    await simulator.mark_watched(redis, shop_id)
    assert await simulator.simulate_tick(get_sessionmaker(), redis, queue) == 1
    await simulator.set_paused(redis, shop_id, True)
    assert await simulator.simulate_tick(get_sessionmaker(), redis, queue) == 0


@pytest.mark.parametrize("scenario", sorted(simulator.SCENARIOS))
async def test_scenarios_via_api(
    scenario: str,
    owner: Session,
    demo_shop: dict[str, str],
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    shop_id = uuid.UUID(demo_shop["id"])
    orders_before = await db.scalar(
        select(func.count()).select_from(Order).where(Order.shop_id == shop_id)
    )
    response = await owner.post(f"/api/shops/{shop_id}/demo/scenarios/{scenario}")
    assert response.status_code == 202, response.text
    again = await owner.post(f"/api/shops/{shop_id}/demo/scenarios/{scenario}")
    assert again.status_code == 409

    orders_after = await db.scalar(
        select(func.count()).select_from(Order).where(Order.shop_id == shop_id)
    )
    new_orders = (orders_after or 0) - (orders_before or 0)
    if scenario == "flash_sale":
        assert new_orders >= 16
        discounted = await db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.shop_id == shop_id, Order.discount_minor > 0, Order.source_name == "instagram"
            )
        )
        assert discounted is not None
        assert discounted >= 16
    elif scenario == "fraud_attempt":
        risky = (
            await db.scalars(
                select(Order).where(
                    Order.shop_id == shop_id, Order.billing_country.in_(HIGH_RISK_COUNTRIES)
                )
            )
        ).all()
        assert len(risky) == 3
        assert all(o.shipping_country == "US" and o.financial_status == "pending" for o in risky)
    elif scenario == "stockout":
        empty = await db.scalar(
            select(func.count())
            .select_from(Variant)
            .where(Variant.shop_id == shop_id, Variant.inventory_quantity == 0)
        )
        assert empty is not None
        assert empty >= 1
    elif scenario == "angry_review":
        review = await db.scalar(
            select(Review).where(Review.shop_id == shop_id, Review.rating == 1)
        )
        assert review is not None
        assert (
            await db.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.shop_id == shop_id)
            )
            == 1
        )
    elif scenario == "shipping_delay":
        tickets = (await db.scalars(select(Ticket).where(Ticket.shop_id == shop_id))).all()
        assert len(tickets) == 4
        assert all(t.subject.startswith("Still waiting on #") for t in tickets)

    kinds = [e.data["kind"] for e in await events.recent(redis, shop_id, 200, {"activity"})]
    assert kinds[0] == "scenario.completed"
    assert kinds[-1] == "scenario.started"


async def test_demo_controls_enforce_roles_and_mode(
    app: FastAPI,
    owner: Session,
    demo_shop: dict[str, str],
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    shop_id = demo_shop["id"]
    assert (await owner.post(f"/api/shops/{shop_id}/demo/scenarios/meteor")).status_code == 404

    paused = await owner.post(f"/api/shops/{shop_id}/demo/simulator", json={"running": False})
    assert paused.json() == {"running": False}
    assert await simulator.is_paused(redis, uuid.UUID(shop_id))
    dashboard = (await owner.get(f"/api/shops/{shop_id}/dashboard")).json()
    assert dashboard["simulator"]["running"] is False

    viewer = await signup(app, "watcher@northbound.example")
    await owner.post(f"/api/shops/{shop_id}/members", json={"email": "watcher@northbound.example"})
    assert (await viewer.get(f"/api/shops/{shop_id}/dashboard")).status_code == 200
    assert (await viewer.post(f"/api/shops/{shop_id}/demo/scenarios/flash_sale")).status_code == 403
    await viewer.client.aclose()

    user_id = uuid.UUID(owner.user_id)
    from app.models import User

    user = await db.get(User, user_id)
    assert user is not None
    live = await create_shop(
        db, owner=user, name="Live", domain="live-shop.example", mode=ShopMode.LIVE
    )
    await db.commit()
    response = await owner.post(f"/api/shops/{live.id}/demo/scenarios/flash_sale")
    assert response.status_code == 409
    assert (await owner.get(f"/api/shops/{live.id}/dashboard")).json()["simulator"] is None
