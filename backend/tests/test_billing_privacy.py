import json
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runner import create_proposal
from app.core import signing
from app.core.crypto import encrypt_secret
from app.models import (
    ActionProposal,
    AgentConfig,
    AuditLog,
    Checkout,
    Customer,
    Message,
    Notification,
    Order,
    Shop,
    ShopSettings,
    Ticket,
    UsageCounter,
    User,
    WebhookEvent,
)
from app.models.base import utcnow
from app.models.enums import (
    AgentName,
    MessageAuthor,
    MessageDirection,
    Plan,
    ProposalStatus,
    RiskLevel,
    ShopMode,
    ShopStatus,
    TicketChannel,
)
from app.services import billing, privacy
from app.services.shops import create_shop
from app.shopify.security import webhook_hmac
from tests.conftest import InlineQueue, Session
from tests.fakes import SECRET, SHOP, FakeShopify


async def _live_shop(db: AsyncSession, owner: Session, plan: Plan = Plan.FREE) -> Shop:
    user = await db.get(User, uuid.UUID(owner.user_id))
    assert user is not None
    shop = await create_shop(db, owner=user, name="Acme", domain=SHOP, mode=ShopMode.LIVE)
    shop.access_token_encrypted = encrypt_secret("shpat_live_token")
    shop.plan = plan
    await db.commit()
    return shop


# ---- signing ----------------------------------------------------------------------------------


def test_signed_tokens_round_trip_and_reject_tampering() -> None:
    token = signing.sign("purpose", {"a": 1})
    assert signing.unsign("purpose", token) == {"a": 1}
    with pytest.raises(signing.BadSignatureError):
        signing.unsign("other-purpose", token)
    with pytest.raises(signing.BadSignatureError):
        signing.unsign("purpose", token[:-2] + "xx")
    expired = signing.sign("purpose", {"a": 1}, max_age_seconds=-1)
    with pytest.raises(signing.BadSignatureError, match="expired"):
        signing.unsign("purpose", expired)


# ---- plans and usage --------------------------------------------------------------------------


async def test_usage_cap_blocks_actions_and_alerts(
    owner: Session, db: AsyncSession, redis: fakeredis.FakeAsyncRedis
) -> None:
    shop = await _live_shop(db, owner, Plan.GROWTH)
    now = utcnow()
    await billing.increment(db, shop.id, billing.AI_ACTIONS, 1_599, now)
    await db.commit()
    await billing.increment(db, shop.id, billing.AI_ACTIONS, 1, now)
    await billing.maybe_alert(db, shop, now)
    await db.commit()
    alerts = (
        await db.scalars(select(Notification).where(Notification.kind == "billing.usage"))
    ).all()
    assert [a.title for a in alerts] == ["80% of this month's AI actions used"]
    await billing.maybe_alert(db, shop, now)  # idempotent within the month
    await db.commit()
    assert (
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.kind == "billing.usage")
        )
        == 1
    )

    await billing.increment(db, shop.id, billing.AI_ACTIONS, 400, now)
    await db.commit()
    reason = await billing.action_limit_reason(db, shop, now)
    assert reason == "Monthly limit of 2,000 AI actions reached on the Growth plan"

    from app.agents.base import ProposalDraft

    settings = (
        await db.execute(select(ShopSettings).where(ShopSettings.shop_id == shop.id))
    ).scalar_one()
    config = (
        await db.execute(
            select(AgentConfig).where(
                AgentConfig.shop_id == shop.id, AgentConfig.agent == AgentName.FRAUD_GUARD
            )
        )
    ).scalar_one()
    proposal = await create_proposal(
        db,
        redis,
        shop,
        settings,
        config,
        None,
        ProposalDraft(
            action_type="hold_order",
            title="Hold #1",
            summary="",
            rationale="",
            risk_level=RiskLevel.MEDIUM,
            payload={"order_id": str(uuid.uuid4())},
        ),
    )
    assert proposal.status == ProposalStatus.REJECTED
    assert proposal.error is not None
    assert "Monthly limit of 2,000 AI actions" in proposal.error

    overview = (await owner.get(f"/api/shops/{shop.id}/billing")).json()
    assert overview["usage"]["ai_actions"] == 2_000
    assert overview["usage"]["ai_actions_limit"] == 2_000
    assert [p["plan"] for p in overview["plans"]] == ["free", "growth", "pro"]


async def test_demo_stores_are_unmetered(
    owner: Session, db: AsyncSession, demo_shop: dict[str, str]
) -> None:
    shop = await db.get(Shop, uuid.UUID(demo_shop["id"]))
    assert shop is not None
    await billing.increment(db, shop.id, billing.AI_ACTIONS, 10_000, utcnow())
    await db.commit()
    assert await billing.action_limit_reason(db, shop, utcnow()) is None
    overview = (await owner.get(f"/api/shops/{shop.id}/billing")).json()
    assert overview["metered"] is False

    switched = await owner.post(f"/api/shops/{shop.id}/billing/subscribe", json={"plan": "pro"})
    assert switched.json() == {"plan": "pro", "confirmation_url": None}
    assert (
        await owner.post(f"/api/shops/{shop.id}/billing/subscribe", json={"plan": "pro"})
    ).status_code == 409


async def test_free_live_store_runs_one_agent(owner: Session, db: AsyncSession) -> None:
    shop = await _live_shop(db, owner)
    await db.execute(
        update(AgentConfig)
        .where(
            AgentConfig.shop_id == shop.id,
            AgentConfig.agent != AgentName.FRAUD_GUARD,
            AgentConfig.agent != AgentName.ORCHESTRATOR,
        )
        .values(enabled=False)
    )
    await db.commit()
    blocked = await owner.patch(f"/api/shops/{shop.id}/agents/support", json={"enabled": True})
    assert blocked.status_code == 402
    assert "Upgrade to Growth" in blocked.json()["detail"]
    # Swapping is fine: turn Fraud Guard off, then another agent on.
    assert (
        await owner.patch(f"/api/shops/{shop.id}/agents/fraud_guard", json={"enabled": False})
    ).status_code == 200
    assert (
        await owner.patch(f"/api/shops/{shop.id}/agents/support", json={"enabled": True})
    ).status_code == 200


async def test_shopify_subscription_flow_and_downgrade(
    owner: Session,
    db: AsyncSession,
    fake_shopify: FakeShopify,
) -> None:
    shop = await _live_shop(db, owner)
    response = await owner.post(f"/api/shops/{shop.id}/billing/subscribe", json={"plan": "growth"})
    assert response.status_code == 200, response.text
    assert response.json()["confirmation_url"].startswith(f"https://{SHOP}/admin/charges/confirm")
    create = next(g for g in fake_shopify.graphql if "appSubscriptionCreate" in g["query"])
    assert create["variables"]["test"] is True
    assert (
        create["variables"]["lineItems"][0]["plan"]["appRecurringPricingDetails"]["price"]["amount"]
        == "49"
    )
    return_url = urlparse(create["variables"]["returnUrl"])
    token = parse_qs(return_url.query)["t"][0]

    declined = await owner.client.get("/api/billing/return", params={"t": token})
    assert declined.status_code == 303
    assert "error=" in declined.headers["location"]

    fake_shopify.active_subscriptions = [
        {"id": "gid://shopify/AppSubscription/1", "name": "StoreOps Growth", "status": "ACTIVE"}
    ]
    approved = await owner.client.get("/api/billing/return", params={"t": token})
    assert approved.headers["location"].endswith(f"/app/{shop.id}/billing?upgraded=growth")
    await db.refresh(shop)
    assert shop.plan == Plan.GROWTH
    assert (
        await owner.client.get("/api/billing/return", params={"t": "forged.token"})
    ).status_code == 400

    # Growth unlocks every agent; downgrading keeps Fraud Guard and switches the rest off.
    await owner.patch(f"/api/shops/{shop.id}/agents/support", json={"enabled": True})
    downgraded = await owner.post(f"/api/shops/{shop.id}/billing/subscribe", json={"plan": "free"})
    assert downgraded.json()["plan"] == "free"
    assert fake_shopify.active_subscriptions == []
    enabled = (
        await db.scalars(
            select(AgentConfig.agent).where(
                AgentConfig.shop_id == shop.id,
                AgentConfig.enabled.is_(True),
                AgentConfig.agent != AgentName.ORCHESTRATOR,
            )
        )
    ).all()
    assert enabled == [AgentName.FRAUD_GUARD]
    changes = (
        await db.scalars(select(AuditLog.details).where(AuditLog.action == "billing.plan_changed"))
    ).all()
    assert [(c["from"], c["to"]) for c in changes] == [("free", "growth"), ("growth", "free")]


async def test_subscription_webhook_updates_plan(
    owner: Session, db: AsyncSession, fake_shopify: FakeShopify
) -> None:
    shop = await _live_shop(db, owner)
    for status, expected in (("ACTIVE", Plan.PRO), ("CANCELLED", Plan.FREE)):
        body = json.dumps(
            {
                "app_subscription": {
                    "admin_graphql_api_id": "gid://1",
                    "name": "StoreOps Pro",
                    "status": status,
                }
            }
        ).encode()
        await owner.client.post(
            "/api/webhooks/shopify",
            content=body,
            headers={
                "x-shopify-topic": "app_subscriptions/update",
                "x-shopify-shop-domain": SHOP,
                "x-shopify-webhook-id": f"sub-{status}",
                "x-shopify-hmac-sha256": webhook_hmac(SECRET, body),
            },
        )
        await db.refresh(shop)
        assert shop.plan == expected


async def test_second_live_store_requires_pro(owner: Session, db: AsyncSession) -> None:
    shop = await _live_shop(db, owner)
    user_id = uuid.UUID(owner.user_id)
    assert await billing.live_store_limit_reason(db, user_id, "second.myshopify.com") is not None
    assert await billing.live_store_limit_reason(db, user_id, SHOP) is None
    shop.plan = Plan.PRO
    await db.commit()
    assert await billing.live_store_limit_reason(db, user_id, "second.myshopify.com") is None


async def test_llm_budget_is_a_hard_cap(owner: Session, db: AsyncSession) -> None:
    shop = await _live_shop(db, owner)
    now = utcnow()
    assert await billing.llm_budget_left(db, shop, now)
    await billing.increment(db, shop.id, billing.LLM_COST, 1_000_001, now)
    await db.commit()
    assert not await billing.llm_budget_left(db, shop, now)


# ---- GDPR -------------------------------------------------------------------------------------


async def _customer_with_history(db: AsyncSession, shop: Shop) -> Customer:
    customer = Customer(
        shop_id=shop.id,
        shopify_id=555,
        email="pat@customers.example",
        first_name="Pat",
        country_code="US",
        accepts_marketing=True,
    )
    db.add(customer)
    await db.flush()
    db.add(
        Order(
            shop_id=shop.id,
            shopify_id=9,
            name="#1009",
            customer_id=customer.id,
            email="pat@customers.example",
            currency="USD",
            total_minor=5_000,
            billing_country="US",
            processed_at=utcnow(),
        )
    )
    db.add(
        Checkout(
            shop_id=shop.id,
            token="tok",
            email="pat@customers.example",
            currency="USD",
            customer_id=customer.id,
        )
    )
    ticket = Ticket(
        shop_id=shop.id,
        channel=TicketChannel.EMAIL,
        subject="Hi",
        customer_email="pat@customers.example",
    )
    db.add(ticket)
    await db.flush()
    db.add(
        Message(
            shop_id=shop.id,
            ticket_id=ticket.id,
            direction=MessageDirection.INBOUND,
            author_type=MessageAuthor.CUSTOMER,
            body="My address is 1 Secret Lane",
        )
    )
    await db.commit()
    return customer


async def _compliance_webhook(owner: Session, topic: str, payload: dict[str, object]) -> int:
    body = json.dumps(payload).encode()
    response = await owner.client.post(
        "/api/webhooks/shopify",
        content=body,
        headers={
            "x-shopify-topic": topic,
            "x-shopify-shop-domain": SHOP,
            "x-shopify-webhook-id": f"{topic}-{uuid.uuid4()}",
            "x-shopify-hmac-sha256": webhook_hmac(SECRET, body),
        },
    )
    return response.status_code


async def test_customer_data_request_export(
    owner: Session,
    db: AsyncSession,
    fake_shopify: FakeShopify,
) -> None:
    shop = await _live_shop(db, owner)
    await _customer_with_history(db, shop)
    status = await _compliance_webhook(
        owner,
        "customers/data_request",
        {
            "customer": {"id": 555, "email": "pat@customers.example"},
            "orders_requested": [9],
            "data_request": {"id": 77},
        },
    )
    assert status == 200
    requests = (await owner.get(f"/api/shops/{shop.id}/privacy/requests")).json()
    assert requests[0]["customer_email"] == "pat@customers.example"
    assert requests[0]["orders"] == 1
    export = await owner.get(
        f"/api/shops/{shop.id}/privacy/requests/{requests[0]['id']}/export.json"
    )
    data = export.json()
    assert data["request_id"] == 77
    assert data["orders"][0]["name"] == "#1009"
    assert data["support"][0]["messages"] == ["My address is 1 Secret Lane"]


async def test_customer_redact_removes_pii(
    owner: Session,
    db: AsyncSession,
    fake_shopify: FakeShopify,
) -> None:
    shop = await _live_shop(db, owner)
    customer = await _customer_with_history(db, shop)
    # Still honoured after the app was uninstalled.
    shop.status = ShopStatus.UNINSTALLED
    await db.commit()
    status = await _compliance_webhook(
        owner,
        "customers/redact",
        {"customer": {"id": 555, "email": "pat@customers.example"}, "orders_to_redact": [9]},
    )
    assert status == 200
    await db.refresh(customer)
    assert customer.email is None
    assert customer.first_name is None
    assert customer.redacted_at is not None
    order = await db.scalar(select(Order).where(Order.shopify_id == 9))
    assert order is not None
    await db.refresh(order)
    assert order.email is None
    message = await db.scalar(select(Message))
    assert message is not None
    await db.refresh(message)
    assert message.body == privacy.REDACTED
    assert (
        await db.scalar(select(AuditLog).where(AuditLog.action == "privacy.customer_redacted"))
        is not None
    )


async def test_shop_redact_and_scheduled_purge_delete_everything(
    owner: Session,
    db: AsyncSession,
    fake_shopify: FakeShopify,
) -> None:
    shop = await _live_shop(db, owner)
    await _customer_with_history(db, shop)
    shop_id = shop.id
    assert await _compliance_webhook(owner, "shop/redact", {"shop_domain": SHOP}) == 200
    db.expire_all()
    assert await db.get(Shop, shop_id) is None
    for model in (Customer, Order, AuditLog, WebhookEvent, UsageCounter):
        assert (
            await db.scalar(select(func.count()).select_from(model).where(model.shop_id == shop_id))
            == 0
        )

    second = await _live_shop(db, owner)
    second.status = ShopStatus.UNINSTALLED
    second.data_deletion_due_at = utcnow() - timedelta(minutes=1)
    await db.commit()
    second_id = second.id
    assert await privacy.purge_due_shops(db) == 1
    db.expire_all()
    assert await db.get(Shop, second_id) is None


async def test_retention_prunes_old_webhook_payloads(
    db: AsyncSession, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    from app.models.enums import EventSource, WebhookStatus

    shop_id = uuid.UUID(demo_shop["id"])
    for days, status in (
        (40, WebhookStatus.PROCESSED),
        (40, WebhookStatus.FAILED),
        (5, WebhookStatus.PROCESSED),
    ):
        db.add(
            WebhookEvent(
                shop_id=shop_id,
                webhook_id=uuid.uuid4().hex,
                topic="orders/create",
                source=EventSource.SHOPIFY,
                payload={},
                status=status,
                received_at=utcnow() - timedelta(days=days),
            )
        )
    await db.commit()
    assert await privacy.apply_retention(db) == 1
    assert await db.scalar(select(func.count()).select_from(WebhookEvent)) == 2


# ---- unsubscribe ------------------------------------------------------------------------------


async def test_unsubscribe_link(
    owner: Session, db: AsyncSession, demo_shop: dict[str, str]
) -> None:
    from app.api.routes.privacy import unsubscribe_token

    customer = await db.scalar(
        select(Customer)
        .where(Customer.shop_id == uuid.UUID(demo_shop["id"]), Customer.accepts_marketing.is_(True))
        .limit(1)
    )
    assert customer is not None
    token = unsubscribe_token(customer.shop_id, customer.id)
    response = await owner.client.post("/api/unsubscribe", json={"token": token})
    assert response.json() == {"status": "unsubscribed"}
    await db.refresh(customer)
    assert customer.unsubscribed_at is not None
    assert not customer.accepts_marketing
    assert (
        await owner.client.post("/api/unsubscribe", json={"token": token + "x"})
    ).status_code == 400
    # Idempotent.
    assert (await owner.client.post("/api/unsubscribe", json={"token": token})).status_code == 200


async def test_cart_recovery_email_carries_working_unsubscribe_link(
    db: AsyncSession, redis: fakeredis.FakeAsyncRedis, queue: InlineQueue, demo_shop: dict[str, str]
) -> None:
    from app.agents.scheduler import scan_abandoned_checkouts
    from app.core.db import get_sessionmaker

    shop_id = uuid.UUID(demo_shop["id"])
    customer = Customer(
        shop_id=shop_id,
        shopify_id=31337,
        email="rae@customers.example",
        first_name="Rae",
        accepts_marketing=True,
    )
    db.add(customer)
    await db.flush()
    db.add(
        Checkout(
            shop_id=shop_id,
            token="abandon-1",
            customer_id=customer.id,
            email=customer.email,
            currency="USD",
            total_minor=4_900,
            line_items=[{"title": "Headlamp", "quantity": 1}],
            shopify_created_at=utcnow() - timedelta(minutes=10),
        )
    )
    await db.commit()
    await scan_abandoned_checkouts(get_sessionmaker(), redis)
    proposal = await db.scalar(
        select(ActionProposal).where(ActionProposal.action_type == "send_recovery_email")
    )
    assert proposal is not None
    link = next(
        line for line in proposal.payload["text"].splitlines() if line.startswith("Unsubscribe:")
    )
    token = parse_qs(urlparse(link.split(" ", 1)[1]).query)["t"][0]
    assert signing.unsign("unsubscribe", token) == {"s": str(shop_id), "c": str(customer.id)}
