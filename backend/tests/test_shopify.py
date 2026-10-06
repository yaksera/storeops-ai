import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.effects import LiveEffects
from app.api.routes.shopify import _normalise_domain
from app.core.crypto import encrypt_secret
from app.models import ActionProposal, AuditLog, Order, Product, Shop, User, WebhookEvent
from app.models.enums import AgentName, ProposalStatus, RiskLevel, ShopMode, ShopStatus
from app.services.shops import create_shop
from app.shopify.client import RateLimiter, ShopifyAuthError, ShopifyClient, ShopifyError
from app.shopify.security import query_hmac, verify_query, verify_webhook, webhook_hmac
from app.shopify.sync import order_payload, product_payload
from tests.conftest import InlineQueue, Session
from tests.fakes import GQL_PRODUCT, SECRET, SHOP, FakeShopify, gql_order, signed

# ---- signatures -------------------------------------------------------------------------------


def test_webhook_and_query_signatures() -> None:
    body = b'{"id": 1}'
    assert verify_webhook(SECRET, body, webhook_hmac(SECRET, body))
    assert not verify_webhook(SECRET, body + b" ", webhook_hmac(SECRET, body))
    assert not verify_webhook(SECRET, body, None)
    params = {"shop": SHOP, "code": "abc", "timestamp": "1", "state": "s"}
    signed_params = {**params, "hmac": query_hmac(SECRET, params)}
    assert verify_query(SECRET, signed_params)
    assert not verify_query(SECRET, {**signed_params, "shop": "evil.myshopify.com"})
    assert not verify_query(SECRET, params)


def test_graphql_payload_mapping() -> None:
    order = order_payload(gql_order())
    assert order["id"] == 4001
    assert order["financial_status"] == "paid"
    assert order["fulfillment_status"] is None
    assert order["line_items"][0]["variant_id"] == 501
    assert order["customer"]["orders_count"] == 3
    product = product_payload(GQL_PRODUCT)
    assert product["variants"][0]["inventory_item_id"] == 601
    assert product["status"] == "active"


# ---- OAuth install ----------------------------------------------------------------------------


async def test_install_requires_configuration(owner: Session) -> None:
    response = await owner.post("/api/shopify/install", json={"shop": "acme"})
    assert response.status_code == 503
    assert (await owner.get("/api/shopify/status")).json()["configured"] is False


async def _start_install(owner: Session) -> dict[str, str]:
    response = await owner.post("/api/shopify/install", json={"shop": "Acme-Outdoors"})
    assert response.status_code == 200, response.text
    url = urlparse(response.json()["url"])
    assert url.netloc == SHOP
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query["client_id"] == "test-client-id"
    assert query["redirect_uri"] == "https://storeops.example/api/shopify/callback"
    params = {
        "shop": SHOP,
        "code": "auth-code",
        "state": query["state"],
        "timestamp": str(int(time.time())),
    }
    return {**params, "hmac": query_hmac(SECRET, params)}


async def test_full_install_creates_live_shop_and_syncs(
    owner: Session, fake_shopify: FakeShopify, db: AsyncSession, queue: InlineQueue
) -> None:
    assert (
        await owner.post("/api/shopify/install", json={"shop": "not a shop!"})
    ).status_code == 422
    params = await _start_install(owner)

    response = await owner.client.get("/api/shopify/callback", params=params)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.endswith("?installed=1")

    shop = await db.scalar(select(Shop).where(Shop.domain == SHOP))
    assert shop is not None
    assert shop.mode == ShopMode.LIVE
    assert shop.name == "Acme Outdoors"
    assert shop.currency == "CAD"
    assert shop.timezone == "America/Toronto"
    assert f"/app/{shop.id}" in location
    assert shop.access_token_encrypted is not None
    assert b"shpat_live_token" not in shop.access_token_encrypted

    webhook_calls = [g for g in fake_shopify.graphql if "webhookSubscriptionCreate" in g["query"]]
    assert len(webhook_calls) == 11
    assert webhook_calls[0]["variables"]["url"] == "https://storeops.example/api/webhooks/shopify"
    assert ("initial_sync", (str(shop.id),), f"sync:{shop.id}") in queue.jobs

    product = await db.scalar(select(Product).where(Product.shop_id == shop.id))
    assert product is not None
    assert product.title == "Trail Mug"
    order = await db.scalar(select(Order).where(Order.shop_id == shop.id))
    assert order is not None
    assert order.total_minor == 12000
    assert order.currency == "USD"

    me = (await owner.get("/api/auth/me")).json()
    assert any(s["domain"] == SHOP and s["role"] == "owner" for s in me["shops"])

    replay = await owner.client.get("/api/shopify/callback", params=params)
    assert replay.status_code == 303
    assert "error=" in replay.headers["location"]


async def test_callback_rejects_bad_signature(owner: Session, fake_shopify: FakeShopify) -> None:
    params = await _start_install(owner)
    params["hmac"] = "0" * 64
    response = await owner.client.get("/api/shopify/callback", params=params)
    assert response.status_code == 400
    assert not any(c.endswith("/access_token") for c in fake_shopify.calls)


# ---- webhooks ---------------------------------------------------------------------------------


async def _live_shop(db: AsyncSession, owner: Session) -> Shop:
    user = await db.get(User, uuid.UUID(owner.user_id))
    assert user is not None
    shop = await create_shop(db, owner=user, name="Acme", domain=SHOP, mode=ShopMode.LIVE)
    shop.access_token_encrypted = encrypt_secret("shpat_live_token")
    await db.commit()
    return shop


async def test_webhook_verification_and_idempotency(
    owner: Session, fake_shopify: FakeShopify, db: AsyncSession
) -> None:
    shop = await _live_shop(db, owner)
    body = json.dumps(order_payload(gql_order())).encode()
    headers = {
        "x-shopify-topic": "orders/create",
        "x-shopify-shop-domain": SHOP,
        "x-shopify-webhook-id": "wh-1",
        "x-shopify-triggered-at": "2026-10-05T10:00:01Z",
        "content-type": "application/json",
    }
    client = owner.client
    bad = await client.post(
        "/api/webhooks/shopify", content=body, headers={**headers, "x-shopify-hmac-sha256": "nope"}
    )
    assert bad.status_code == 401

    for _ in range(2):
        ok = await client.post(
            "/api/webhooks/shopify", content=body, headers={**headers, **signed(body)}
        )
        assert ok.status_code == 200
    assert await db.scalar(select(func.count()).select_from(WebhookEvent)) == 1
    event = await db.scalar(select(WebhookEvent))
    assert event is not None
    assert event.triggered_at is not None
    assert event.status.value == "processed"
    assert (
        await db.scalar(select(func.count()).select_from(Order).where(Order.shop_id == shop.id))
        == 1
    )

    unknown = {
        **headers,
        "x-shopify-shop-domain": "other.myshopify.com",
        "x-shopify-webhook-id": "wh-2",
    }
    assert (
        await client.post(
            "/api/webhooks/shopify", content=body, headers={**unknown, **signed(body)}
        )
    ).status_code == 200
    assert await db.scalar(select(func.count()).select_from(WebhookEvent)) == 1

    missing = {k: v for k, v in headers.items() if k != "x-shopify-topic"}
    assert (
        await client.post(
            "/api/webhooks/shopify", content=body, headers={**missing, **signed(body)}
        )
    ).status_code == 400


async def test_uninstall_revokes_access_and_stops_work(
    owner: Session, fake_shopify: FakeShopify, db: AsyncSession
) -> None:
    shop = await _live_shop(db, owner)
    db.add(
        ActionProposal(
            shop_id=shop.id,
            agent=AgentName.FRAUD_GUARD,
            action_type="hold_order",
            title="Hold",
            risk_level=RiskLevel.MEDIUM,
            payload={},
        )
    )
    await db.commit()
    body = json.dumps({"id": 1, "domain": SHOP}).encode()
    headers = {
        "x-shopify-topic": "app/uninstalled",
        "x-shopify-shop-domain": SHOP,
        "x-shopify-webhook-id": "wh-uninstall",
        **signed(body),
    }
    assert (
        await owner.client.post("/api/webhooks/shopify", content=body, headers=headers)
    ).status_code == 200
    await db.refresh(shop)
    assert shop.status == ShopStatus.UNINSTALLED
    assert shop.access_token_encrypted is None
    assert shop.data_deletion_due_at is not None
    assert (shop.data_deletion_due_at - shop.uninstalled_at).total_seconds() == 48 * 3600  # type: ignore[operator]
    proposal = await db.scalar(select(ActionProposal).where(ActionProposal.shop_id == shop.id))
    assert proposal is not None
    await db.refresh(proposal)
    assert proposal.status == ProposalStatus.EXPIRED
    assert (
        await db.scalar(select(AuditLog).where(AuditLog.action == "shopify.uninstalled"))
        is not None
    )
    assert (await owner.get(f"/api/shops/{shop.id}")).status_code == 410

    late = json.dumps(order_payload(gql_order())).encode()
    late_headers = {
        **headers,
        "x-shopify-topic": "orders/create",
        "x-shopify-webhook-id": "wh-late",
        **signed(late),
    }
    await owner.client.post("/api/webhooks/shopify", content=late, headers=late_headers)
    assert (
        await db.scalar(select(func.count()).select_from(Order).where(Order.shop_id == shop.id))
        == 0
    )


# ---- GraphQL client and effects ---------------------------------------------------------------


async def test_client_retries_throttled_and_tracks_bucket(
    fake_shopify: FakeShopify, redis: fakeredis.FakeAsyncRedis
) -> None:
    fake_shopify.throttle_first = True
    async with ShopifyClient(SHOP, "token", redis) as client:
        data = await client.query("query { shop { name currencyCode ianaTimezone } }")
    assert data["shop"]["name"] == "Acme Outdoors"
    assert len(fake_shopify.graphql) == 2
    state = json.loads(await redis.get(f"shopify:{SHOP}:bucket"))
    assert state["available"] == 1900
    assert state["restore_rate"] == 100


async def test_rate_limiter_waits_for_capacity(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("app.shopify.client.asyncio.sleep", fake_sleep)
    limiter = RateLimiter(redis, SHOP)
    assert await limiter.wait_for(100) == 0.0
    await limiter.record({"maximumAvailable": 1000, "currentlyAvailable": 10, "restoreRate": 50})
    delay = await limiter.wait_for(110)
    assert 1.9 < delay <= 2.0
    assert waits == [delay]


async def test_client_raises_on_revoked_token(
    fake_shopify: FakeShopify, redis: fakeredis.FakeAsyncRedis
) -> None:
    fake_shopify.reject_token = True
    async with ShopifyClient(SHOP, "token", redis) as client:
        with pytest.raises(ShopifyAuthError):
            await client.query("query { shop { name } }")


async def test_live_effects_hold_discount_and_email(
    owner: Session, fake_shopify: FakeShopify, db: AsyncSession, redis: fakeredis.FakeAsyncRedis
) -> None:
    from datetime import UTC, datetime, timedelta

    shop = await _live_shop(db, owner)
    effects = LiveEffects(shop, redis)
    order = Order(shopify_id=4001, name="#1001")
    await effects.hold_order(order, "Scored 90/100")
    holds = [g for g in fake_shopify.graphql if "fulfillmentOrderHold" in g["query"]]
    assert [h["variables"]["id"] for h in holds] == ["gid://FO/1"]
    assert holds[0]["variables"]["hold"]["reason"] == "HIGH_RISK_OF_FRAUD"
    assert any("tagsAdd" in g["query"] for g in fake_shopify.graphql)

    code = await effects.create_discount(pct=10, expires_at=datetime.now(UTC) + timedelta(hours=48))
    assert code.startswith("COMEBACK-")
    discount = next(g for g in fake_shopify.graphql if "discountCodeBasicCreate" in g["query"])
    assert discount["variables"]["input"]["customerGets"]["value"]["percentage"] == 0.1
    assert discount["variables"]["input"]["usageLimit"] == 1

    message_id = await effects.send_email(
        to="pat@customers.example", subject="Hi", text="Body", category="cart_recovery"
    )
    assert message_id == "email-123"


async def test_shopify_errors_surface_user_errors(
    fake_shopify: FakeShopify, redis: fakeredis.FakeAsyncRedis
) -> None:
    from app.shopify.client import user_errors

    with pytest.raises(ShopifyError, match="Code taken"):
        user_errors(
            {"discountCodeBasicCreate": {"userErrors": [{"message": "Code taken"}]}},
            "discountCodeBasicCreate",
        )


async def test_nightly_reconciliation_resyncs_live_shops(
    owner: Session, fake_shopify: FakeShopify, db: AsyncSession, queue: InlineQueue
) -> None:
    from app.worker.tasks import reconcile_shops

    shop = await _live_shop(db, owner)
    assert await reconcile_shops({}) == 1
    assert (
        await db.scalar(select(func.count()).select_from(Order).where(Order.shop_id == shop.id))
        == 1
    )
    # A second pass with the same Shopify versions is deduplicated.
    assert await reconcile_shops({}) == 1
    assert (
        await db.scalar(
            select(func.count()).select_from(WebhookEvent).where(WebhookEvent.shop_id == shop.id)
        )
        == 2
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme-Outdoors", "acme-outdoors.myshopify.com"),
        ("acme-outdoors.myshopify.com", "acme-outdoors.myshopify.com"),
        ("https://acme-outdoors.myshopify.com/admin", "acme-outdoors.myshopify.com"),
        ("https://admin.shopify.com/store/abc123-xy/orders", "abc123-xy.myshopify.com"),
    ],
)
def test_shop_domain_normalisation(raw: str, expected: str) -> None:
    assert _normalise_domain(raw) == expected
