import json
import random
import uuid
from datetime import timedelta
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runner
from app.agents.effects import outbox_key
from app.core.db import get_sessionmaker
from app.demo.simulator import DemoStore, Line
from app.llm.client import LlmClient
from app.models import (
    ActionProposal,
    AgentConfig,
    AgentRun,
    Approval,
    AuditLog,
    Checkout,
    Customer,
    Notification,
    Order,
    Shop,
    ShopSettings,
    UsageCounter,
    User,
    Variant,
)
from app.models.base import utcnow
from app.models.enums import (
    ActorType,
    AgentName,
    Autonomy,
    CheckoutStatus,
    ProposalStatus,
    RiskLevel,
    RunStatus,
    ShopMode,
)
from app.services import events
from app.services.shops import create_shop
from tests.conftest import InlineQueue, Session, signup


async def _shop(db: AsyncSession, data: dict[str, str]) -> Shop:
    shop = await db.get(Shop, uuid.UUID(data["id"]))
    assert shop is not None
    return shop


async def _store(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, shop: Shop
) -> DemoStore:
    return await DemoStore(shop, db, redis, queue, rng=random.Random(3)).load()


async def _set_agent(db: AsyncSession, shop_id: uuid.UUID, agent: AgentName, **values: Any) -> None:
    await db.execute(
        update(AgentConfig)
        .where(AgentConfig.shop_id == shop_id, AgentConfig.agent == agent)
        .values(**values)
    )
    await db.commit()


async def _set_shop(db: AsyncSession, shop_id: uuid.UUID, **values: Any) -> None:
    await db.execute(update(ShopSettings).where(ShopSettings.shop_id == shop_id).values(**values))
    await db.commit()


async def _proposals(db: AsyncSession, shop_id: uuid.UUID, action: str) -> list[ActionProposal]:
    return list(
        (
            await db.scalars(
                select(ActionProposal)
                .where(ActionProposal.shop_id == shop_id, ActionProposal.action_type == action)
                .order_by(ActionProposal.created_at)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def _fraud_order(store: DemoStore) -> Order:
    name = await store.fraudulent_order()
    assert name is not None
    order = await store.db.scalar(
        select(Order).where(Order.shop_id == store.shop.id, Order.name == name)
    )
    assert order is not None
    await store.db.refresh(order)
    return order


# ---- Fraud Guard ----------------------------------------------------------------------------


async def test_fraud_guard_scores_and_proposes_hold(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    order = await _fraud_order(await _store(db, redis, queue, shop))
    assert order.risk_score is not None
    assert order.risk_score >= 70
    assert not order.is_held

    [proposal] = await _proposals(db, shop.id, "hold_order")
    assert proposal.status == ProposalStatus.PROPOSED
    assert proposal.requires_approval
    assert proposal.summary.startswith(f"Scored {order.risk_score}/100: billed in")
    assert proposal.preview["factors"]

    run = await db.scalar(select(AgentRun).where(AgentRun.agent == AgentName.FRAUD_GUARD))
    assert run is not None
    assert run.status == RunStatus.SUCCEEDED
    assert run.output is not None
    assert run.output["score"] == order.risk_score
    assert run.prompt_version == "rules-v1"

    alert = await db.scalar(select(Notification).where(Notification.kind == "fraud.alert"))
    assert alert is not None
    updates = [e for e in await events.recent(redis, shop.id, 300) if e.type == "order.updated"]
    assert any(e.data["risk_score"] == order.risk_score for e in updates)
    created = [e for e in await events.recent(redis, shop.id, 300) if e.type == "proposal.created"]
    assert created[0].data["action_type"] == "hold_order"


async def test_clean_order_gets_low_score_and_no_proposal(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    store = await _store(db, redis, queue, shop)
    customer = await store.random_customer()
    name = await store.place_order(customer, [Line(store.stock[0], 1)])
    order = await db.scalar(select(Order).where(Order.shop_id == shop.id, Order.name == name))
    assert order is not None
    await db.refresh(order)
    assert order.risk_score is not None
    assert order.risk_score < 70
    assert await _proposals(db, shop.id, "hold_order") == []


async def test_approve_hold_executes_and_audits(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    order = await _fraud_order(await _store(db, redis, queue, shop))
    [proposal] = await _proposals(db, shop.id, "hold_order")

    listing = (await owner.get(f"/api/shops/{shop.id}/proposals?status=proposed")).json()
    assert listing["pending"] == 1
    assert listing["items"][0]["id"] == str(proposal.id)
    dashboard = (await owner.get(f"/api/shops/{shop.id}/dashboard")).json()
    assert dashboard["pending_approvals"] == 1

    response = await owner.post(
        f"/api/shops/{shop.id}/proposals/{proposal.id}/approve",
        json={"comment": "Looks fraudulent"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "executed"

    await db.refresh(order)
    assert order.is_held
    approval = await db.scalar(select(Approval).where(Approval.proposal_id == proposal.id))
    assert approval is not None
    assert approval.comment == "Looks fraudulent"
    assert str(approval.user_id) == owner.user_id

    actions = (
        await db.scalars(
            select(AuditLog.action)
            .where(AuditLog.target_id == str(proposal.id))
            .order_by(AuditLog.id)
        )
    ).all()
    assert actions == ["proposal.created", "proposal.approved", "action.executed"]
    executed = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "action.executed", AuditLog.target_id == str(proposal.id)
        )
    )
    assert executed is not None
    assert executed.actor_id == owner.user_id
    usage = await db.scalar(select(UsageCounter.value).where(UsageCounter.shop_id == shop.id))
    assert usage == 1
    outbox = [json.loads(x) for x in await redis.lrange(outbox_key(shop.id), 0, -1)]  # type: ignore[misc]
    assert outbox[0]["kind"] == "hold_order"

    again = await owner.post(f"/api/shops/{shop.id}/proposals/{proposal.id}/approve")
    assert again.status_code == 409
    dashboard = (await owner.get(f"/api/shops/{shop.id}/dashboard")).json()
    fraud = next(a for a in dashboard["agents"] if a["agent"] == "fraud_guard")
    assert fraud["actions_today"] == 1


async def test_reject_and_permissions(
    app: FastAPI,
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    order = await _fraud_order(await _store(db, redis, queue, shop))
    [proposal] = await _proposals(db, shop.id, "hold_order")

    viewer = await signup(app, "viewer2@northbound.example")
    await owner.post(f"/api/shops/{shop.id}/members", json={"email": "viewer2@northbound.example"})
    assert (await viewer.get(f"/api/shops/{shop.id}/proposals")).status_code == 200
    assert (
        await viewer.post(f"/api/shops/{shop.id}/proposals/{proposal.id}/approve")
    ).status_code == 403
    await viewer.client.aclose()

    bad_edit = await owner.post(
        f"/api/shops/{shop.id}/proposals/{proposal.id}/approve", json={"edits": {"order_id": "x"}}
    )
    assert bad_edit.status_code == 422
    rejected = await owner.post(f"/api/shops/{shop.id}/proposals/{proposal.id}/reject", json={})
    assert rejected.json()["status"] == "rejected"
    await db.refresh(order)
    assert not order.is_held
    missing = await owner.post(f"/api/shops/{shop.id}/proposals/{uuid.uuid4()}/approve")
    assert missing.status_code == 404


async def test_auto_mode_holds_without_approval(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    await _set_agent(db, shop.id, AgentName.FRAUD_GUARD, autonomy=Autonomy.AUTO)
    order = await _fraud_order(await _store(db, redis, queue, shop))
    [proposal] = await _proposals(db, shop.id, "hold_order")
    assert proposal.status == ProposalStatus.EXECUTED
    assert not proposal.requires_approval
    await db.refresh(order)
    assert order.is_held


async def test_dry_run_records_without_side_effects(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    await _set_agent(db, shop.id, AgentName.FRAUD_GUARD, autonomy=Autonomy.AUTO)
    await _set_shop(db, shop.id, dry_run=True)
    order = await _fraud_order(await _store(db, redis, queue, shop))
    [proposal] = await _proposals(db, shop.id, "hold_order")
    assert proposal.status == ProposalStatus.EXECUTED
    assert proposal.result == {"dry_run": True}
    await db.refresh(order)
    assert not order.is_held
    assert await redis.llen(outbox_key(shop.id)) == 0  # type: ignore[misc]


async def test_kill_switch_stops_agents(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    await _set_shop(db, shop.id, kill_switch=True)
    await _fraud_order(await _store(db, redis, queue, shop))
    assert await _proposals(db, shop.id, "hold_order") == []
    run = await db.scalar(select(AgentRun).where(AgentRun.agent == AgentName.FRAUD_GUARD))
    assert run is not None
    assert run.status == RunStatus.SKIPPED


async def test_daily_cap_blocks_proposal(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    await _set_agent(db, shop.id, AgentName.FRAUD_GUARD, daily_action_cap=0)
    await _fraud_order(await _store(db, redis, queue, shop))
    [proposal] = await _proposals(db, shop.id, "hold_order")
    assert proposal.status == ProposalStatus.REJECTED
    assert proposal.error == "Blocked by guardrails: Daily cap of 0 actions reached"
    blocked = await db.scalar(select(AuditLog).where(AuditLog.action == "guardrail.blocked"))
    assert blocked is not None


async def test_agent_error_is_isolated(
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = await _shop(db, demo_shop)

    async def boom(*args: object) -> None:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(runner.REGISTRY[AgentName.FRAUD_GUARD], "decide", boom)
    store = await _store(db, redis, queue, shop)
    name = await store.place_order(await store.random_customer(), [Line(store.stock[0], 1)])
    order = await db.scalar(select(Order).where(Order.shop_id == shop.id, Order.name == name))
    assert order is not None  # the pipeline still committed the order

    failed = await db.scalar(select(AgentRun).where(AgentRun.agent == AgentName.FRAUD_GUARD))
    assert failed is not None
    assert failed.status == RunStatus.FAILED
    assert failed.error == "RuntimeError: model exploded"
    planner = await db.scalar(
        select(func.count(AgentRun.id)).where(AgentRun.agent == AgentName.INVENTORY_PLANNER)
    )
    assert planner == 1


async def test_live_store_without_integrations_fails_cleanly(
    owner: Session, db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue
) -> None:
    user = await db.get(User, uuid.UUID(owner.user_id))
    assert user is not None
    shop = await create_shop(db, owner=user, name="Live", domain="live.example", mode=ShopMode.LIVE)
    await db.commit()
    proposal = ActionProposal(
        shop_id=shop.id,
        agent=AgentName.FRAUD_GUARD,
        action_type="hold_order",
        title="Hold",
        risk_level=RiskLevel.MEDIUM,
        payload={"order_id": str(uuid.uuid4())},
        status=ProposalStatus.APPROVED,
    )
    db.add(proposal)
    await db.commit()
    result = await runner.execute_proposal(
        db,
        redis,
        shop,
        proposal,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
    )
    assert result.status == ProposalStatus.EXPIRED
    assert result.error == "Order no longer exists"


async def test_expire_proposals(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    await _fraud_order(await _store(db, redis, queue, shop))
    assert await runner.expire_proposals(db, redis, utcnow() + timedelta(hours=13)) == 1
    [proposal] = await _proposals(db, shop.id, "hold_order")
    assert proposal.status == ProposalStatus.EXPIRED


# ---- Inventory Planner ------------------------------------------------------------------------


async def _drain(store: DemoStore, sku: str, leave: int) -> Variant:
    item = next(i for i in store.stock if i.sku == sku)
    await store.place_order(await store.random_customer(), [Line(item, item.quantity - leave)])
    variant = await store.db.scalar(
        select(Variant).where(Variant.shop_id == store.shop.id, Variant.sku == sku)
    )
    assert variant is not None
    return variant


async def test_inventory_planner_drafts_single_po_and_sends_on_approval(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    await _set_agent(db, shop.id, AgentName.INVENTORY_PLANNER, autonomy=Autonomy.AUTO)
    store = await _store(db, redis, queue, shop)
    variant = await _drain(store, "NB-SOCK-M", leave=5)
    item = next(i for i in store.stock if i.sku == "NB-SOCK-M")
    await store.place_order(await store.random_customer(), [Line(item, 1)])

    [po] = await _proposals(db, shop.id, "draft_po")
    assert po.status == ProposalStatus.PROPOSED  # POs always need approval, even in AUTO
    assert po.target_id == str(variant.id)
    assert po.payload["quantity"] > 0
    assert po.payload["to"] == "purchasing@northbound-supply.example"
    assert "Merino Hiking Socks" in po.title

    response = await owner.post(
        f"/api/shops/{shop.id}/proposals/{po.id}/approve", json={"edits": {"quantity": 150}}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "executed"
    assert body["result"]["quantity"] == 150
    outbox = [json.loads(x) for x in await redis.lrange(outbox_key(shop.id), 0, -1)]  # type: ignore[misc]
    assert outbox[0]["category"] == "purchase_order"
    assert outbox[0]["to"] == "purchasing@northbound-supply.example"

    inventory = (await owner.get(f"/api/shops/{shop.id}/inventory")).json()
    row = next(i for i in inventory["items"] if i["sku"] == "NB-SOCK-M")
    assert row["on_hand"] == 4
    assert row["needs_reorder"]
    assert row["days_to_stockout"] is not None


async def test_inventory_bad_edit_rejected(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    await _drain(await _store(db, redis, queue, shop), "NB-SOCK-S", leave=2)
    [po] = await _proposals(db, shop.id, "draft_po")
    response = await owner.post(
        f"/api/shops/{shop.id}/proposals/{po.id}/approve", json={"edits": {"quantity": -3}}
    )
    assert response.status_code == 422


# ---- Cart Recovery ----------------------------------------------------------------------------


async def _abandoned_checkout(
    db: AsyncSession, shop: Shop, *, consent: bool = True, minutes_ago: int = 10
) -> Checkout:
    customer = Customer(
        shop_id=shop.id,
        shopify_id=int(uuid.uuid4().int % 10**12),
        email="rowan@customers.example",
        first_name="Rowan",
        accepts_marketing=consent,
    )
    db.add(customer)
    await db.flush()
    checkout = Checkout(
        shop_id=shop.id,
        token=uuid.uuid4().hex,
        customer_id=customer.id,
        email=customer.email,
        currency="USD",
        total_minor=24_900,
        line_items=[{"title": "Ridgeline Down Jacket - M", "quantity": 1, "price_minor": 24_900}],
        recovery_url=f"https://{shop.domain}/checkouts/abc/recover",
        status=CheckoutStatus.OPEN,
        shopify_created_at=utcnow() - timedelta(minutes=minutes_ago),
    )
    db.add(checkout)
    await db.commit()
    return checkout


async def _scan(redis: fakeredis.FakeAsyncRedis) -> int:
    from app.agents.scheduler import scan_abandoned_checkouts

    return await scan_abandoned_checkouts(get_sessionmaker(), redis)


async def test_cart_recovery_sends_two_reminders_then_stops(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    checkout = await _abandoned_checkout(db, shop)
    fresh = await _abandoned_checkout(db, shop, minutes_ago=0)

    assert await _scan(redis) == 1
    [first] = await _proposals(db, shop.id, "send_recovery_email")
    assert first.status == ProposalStatus.EXECUTED  # demo config runs Cart Recovery in AUTO
    assert first.payload["discount"] is None
    assert "Ridgeline Down Jacket" in first.payload["text"]
    assert "Unsubscribe:" in first.payload["text"]
    assert "1200 Trailhead Way" in first.payload["text"]
    await db.refresh(checkout)
    assert checkout.reminders_sent == 1
    assert checkout.status == CheckoutStatus.ABANDONED

    assert await _scan(redis) == 0  # reminder interval not reached yet
    checkout.last_reminder_at = utcnow() - timedelta(minutes=30)
    await db.commit()
    assert await _scan(redis) == 1
    second = (await _proposals(db, shop.id, "send_recovery_email"))[-1]
    assert second.status == ProposalStatus.EXECUTED
    assert second.payload["discount"] == {"pct": 10, "valid_hours": 48}
    assert second.result is not None
    code = second.result["discount_code"]
    assert code.startswith("COMEBACK-")
    outbox = [json.loads(x) for x in await redis.lrange(outbox_key(shop.id), 0, -1)]  # type: ignore[misc]
    assert [o["kind"] for o in outbox][:2] == ["email", "discount"]

    checkout.last_reminder_at = utcnow() - timedelta(hours=2)
    await db.commit()
    assert await _scan(redis) == 0
    await db.refresh(fresh)
    assert fresh.reminders_sent == 0

    recovery = (await owner.get(f"/api/shops/{shop.id}/recovery")).json()
    assert recovery["stats"]["emails_sent_14d"] >= 2
    cart = next(c for c in recovery["carts"] if c["id"] == str(checkout.id))
    assert cart["reminders_sent"] == 2
    assert cart["consent"] is True


async def test_cart_recovery_respects_consent(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    await _abandoned_checkout(db, shop, consent=False)
    assert await _scan(redis) == 1
    assert await _proposals(db, shop.id, "send_recovery_email") == []
    run = await db.scalar(select(AgentRun).where(AgentRun.agent == AgentName.CART_RECOVERY))
    assert run is not None
    assert run.output is not None
    assert run.output["skipped"] == "no marketing consent"


async def test_completed_order_cancels_pending_recovery(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    shop = await _shop(db, demo_shop)
    await _set_agent(db, shop.id, AgentName.CART_RECOVERY, autonomy=Autonomy.SUGGEST)
    checkout = await _abandoned_checkout(db, shop)
    await _scan(redis)
    [proposal] = await _proposals(db, shop.id, "send_recovery_email")
    assert proposal.status == ProposalStatus.PROPOSED

    store = await _store(db, redis, queue, shop)
    customer = await db.get(Customer, checkout.customer_id)
    assert customer is not None
    await store.place_order(
        {"id": customer.shopify_id, "email": customer.email, "first_name": "Rowan"},
        [Line(store.stock[0], 1)],
        checkout_token=checkout.token,
    )
    [proposal] = await _proposals(db, shop.id, "send_recovery_email")
    assert proposal.status == ProposalStatus.EXPIRED
    await db.refresh(checkout)
    assert checkout.status == CheckoutStatus.COMPLETED


async def test_reminded_cart_that_converts_counts_as_recovered(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    checkout = await _abandoned_checkout(db, shop)
    await _scan(redis)
    store = await _store(db, redis, queue, shop)
    store.rng = random.Random(0)
    store.rng.random = lambda: 0.0  # type: ignore[method-assign]
    customer = await db.get(Customer, checkout.customer_id)
    assert customer is not None
    item = next(i for i in store.stock if i.sku == "NB-RDJ-M")
    checkout.line_items = [
        {"variant_id": item.variant_shopify_id, "quantity": 1, "title": "Jacket"}
    ]
    await db.commit()
    assert await store.maybe_recover_carts() == 1
    await db.refresh(checkout)
    assert checkout.status == CheckoutStatus.RECOVERED
    assert checkout.recovered_order_id is not None
    kpis = (await owner.get(f"/api/shops/{shop.id}/dashboard")).json()["kpis"]
    assert kpis["recovered_revenue_minor"] > 0


# ---- APIs -------------------------------------------------------------------------------------


async def test_bulk_approve(
    owner: Session,
    db: AsyncSession,
    redis: fakeredis.FakeAsyncRedis,
    queue: InlineQueue,
    demo_shop: dict[str, str],
) -> None:
    shop = await _shop(db, demo_shop)
    store = await _store(db, redis, queue, shop)
    await _fraud_order(store)
    await _fraud_order(store)
    ids = [str(p.id) for p in await _proposals(db, shop.id, "hold_order")]
    assert len(ids) == 2
    response = await owner.post(
        f"/api/shops/{shop.id}/proposals/bulk-approve", json={"ids": [*ids, str(uuid.uuid4())]}
    )
    body = response.json()
    assert sorted(body["approved"]) == sorted(ids)
    assert list(body["errors"].values()) == ["Proposal not found"]


async def test_agent_settings_api(
    owner: Session, db: AsyncSession, demo_shop: dict[str, str]
) -> None:
    base = f"/api/shops/{demo_shop['id']}/agents"
    agents = (await owner.get(base)).json()
    assert len(agents) == 8
    cart = next(a for a in agents if a["agent"] == "cart_recovery")
    assert cart["available"]
    assert cart["autonomy"] == "auto"
    support = next(a for a in agents if a["agent"] == "support")
    assert not support["available"]

    updated = await owner.patch(
        f"{base}/cart_recovery",
        json={"autonomy": "suggest", "daily_action_cap": 20, "settings": {"discount_pct": 5}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["settings"]["discount_pct"] == 5
    assert updated.json()["settings"]["abandon_after_minutes"] == 2

    for bad in (
        {"settings": {"discount_pct": 25}},
        {"settings": {"unknown": 1}},
        {"autonomy": "sometimes"},
    ):
        assert (await owner.patch(f"{base}/cart_recovery", json=bad)).status_code == 422
    assert (
        await owner.patch(f"{base}/pricing_advisor", json={"autonomy": "auto"})
    ).status_code == 422
    assert (await owner.patch(f"{base}/fraud_guard", json={"settings": {}})).status_code == 422
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "agent.updated"))
    assert audit is not None


async def test_agent_test_endpoint_does_not_persist(
    owner: Session, db: AsyncSession, demo_shop: dict[str, str]
) -> None:
    base = f"/api/shops/{demo_shop['id']}/agents"
    result = await owner.post(f"{base}/fraud_guard/test")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["event"]["kind"] == "order.created"
    assert "score" in body["output"]
    inventory = (await owner.post(f"{base}/inventory_planner/test")).json()
    assert "days_to_stockout" in inventory["output"]
    assert (await owner.post(f"{base}/support/test")).status_code == 409
    assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert await db.scalar(select(func.count()).select_from(ActionProposal)) == 0


async def test_audit_api_filters_paginates_and_exports(
    owner: Session, demo_shop: dict[str, str]
) -> None:
    base = f"/api/shops/{demo_shop['id']}"
    for threshold in (71, 72, 73):
        await owner.patch(f"{base}/settings", json={"fraud_threshold": threshold})

    page = (await owner.get(f"{base}/audit?limit=2")).json()
    assert [i["action"] for i in page["items"]] == ["settings.updated", "settings.updated"]
    assert page["next_before_id"] is not None
    rest = (await owner.get(f"{base}/audit?limit=10&before_id={page['next_before_id']}")).json()
    assert [i["action"] for i in rest["items"]] == ["settings.updated", "shop.created"]
    assert rest["next_before_id"] is None

    filtered = (await owner.get(f"{base}/audit?action=shop.&actor_type=user")).json()
    assert [i["action"] for i in filtered["items"]] == ["shop.created"]

    export = await owner.get(f"{base}/audit/export.csv?action=settings")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    lines = export.text.strip().splitlines()
    assert lines[0].startswith("id,occurred_at,actor_type")
    assert len(lines) == 4


# ---- LLM client -------------------------------------------------------------------------------


async def test_llm_client_parses_and_logs_cost(
    db: AsyncSession, demo_shop: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import BaseModel, SecretStr

    from app.core.config import get_settings
    from app.models import LlmCall

    class Out(BaseModel):
        explanation: str

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"explanation": "Billed abroad."}'}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
        )

    settings = get_settings().model_copy(
        update={"openrouter_api_key": SecretStr("test-key"), "llm_max_retries": 1}
    )
    monkeypatch.setattr("app.llm.client.asyncio.sleep", _no_sleep)
    client = LlmClient(
        settings, db, uuid.UUID(demo_shop["id"]), transport=httpx.MockTransport(handler)
    )
    result = await client.complete_json(
        agent=AgentName.FRAUD_GUARD, system="s", user="u", schema=Out, prompt_version="t-1"
    )
    await client.aclose()
    assert result is not None
    assert result.value.explanation == "Billed abroad."
    assert str(result.cost_usd) == "0.000800"
    await db.commit()
    rows = (await db.scalars(select(LlmCall).order_by(LlmCall.created_at))).all()
    assert [r.status.value for r in rows] == ["error", "ok"]
    assert rows[1].input_tokens == 1000


async def test_llm_client_returns_none_on_invalid_output(
    db: AsyncSession, demo_shop: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import BaseModel, SecretStr

    from app.core.config import get_settings

    class Out(BaseModel):
        explanation: str

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    settings = get_settings().model_copy(
        update={"openrouter_api_key": SecretStr("test-key"), "llm_max_retries": 0}
    )
    client = LlmClient(
        settings, db, uuid.UUID(demo_shop["id"]), transport=httpx.MockTransport(handler)
    )
    assert (
        await client.complete_json(
            agent=AgentName.FRAUD_GUARD, system="s", user="u", schema=Out, prompt_version="t-1"
        )
        is None
    )
    await client.aclose()


async def _no_sleep(_: float) -> None:
    return None
