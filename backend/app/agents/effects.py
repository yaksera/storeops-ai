"""Side effects agents can cause in the outside world.

Demo stores get `DemoEffects`, which only records what would have happened (in Redis, for the
dashboard) and never contacts Shopify or an email provider.
"""

import json
import secrets
import uuid
from datetime import datetime
from typing import Protocol

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.models import Order, Review, Shop, Variant
from app.models.base import utcnow
from app.models.enums import ShopMode
from app.shopify.client import ShopifyClient, http_client, user_errors


class EffectsUnavailableError(Exception):
    pass


class Effects(Protocol):
    async def hold_order(self, order: Order, reason: str) -> None: ...

    async def send_email(self, *, to: str, subject: str, text: str, category: str) -> str: ...

    async def create_discount(self, *, pct: int, expires_at: datetime) -> str: ...

    async def reply_to_review(self, review: Review, reply: str) -> None: ...

    async def set_variant_price(
        self, product_shopify_id: int, variant: Variant, price_minor: int
    ) -> None: ...


def outbox_key(shop_id: uuid.UUID) -> str:
    return f"demo:{shop_id}:outbox"


class DemoEffects:
    def __init__(self, shop: Shop, redis: Redis) -> None:
        self.shop = shop
        self.redis = redis

    async def _record(self, entry: dict[str, object]) -> None:
        key = outbox_key(self.shop.id)
        await self.redis.lpush(key, json.dumps({**entry, "at": utcnow().isoformat()}, default=str))  # type: ignore[misc]
        await self.redis.ltrim(key, 0, 199)  # type: ignore[misc]

    async def hold_order(self, order: Order, reason: str) -> None:
        await self._record({"kind": "hold_order", "order": order.name, "reason": reason})

    async def send_email(self, *, to: str, subject: str, text: str, category: str) -> str:
        message_id = f"demo-{uuid.uuid4().hex[:16]}"
        await self._record(
            {"kind": "email", "id": message_id, "to": to, "subject": subject, "category": category}
        )
        return message_id

    async def create_discount(self, *, pct: int, expires_at: datetime) -> str:
        code = f"COMEBACK-{secrets.token_hex(3).upper()}"
        await self._record(
            {"kind": "discount", "code": code, "pct": pct, "expires_at": expires_at.isoformat()}
        )
        return code

    async def reply_to_review(self, review: Review, reply: str) -> None:
        await self._record({"kind": "review_reply", "review": str(review.id), "reply": reply})

    async def set_variant_price(
        self, product_shopify_id: int, variant: Variant, price_minor: int
    ) -> None:
        await self._record({"kind": "price_change", "sku": variant.sku, "price_minor": price_minor})


class UnconfiguredEffects:
    """Live stores until the Shopify and email integrations are connected."""

    async def hold_order(self, order: Order, reason: str) -> None:
        raise EffectsUnavailableError("Shopify connection is not configured")

    async def send_email(self, *, to: str, subject: str, text: str, category: str) -> str:
        raise EffectsUnavailableError("Email provider is not configured")

    async def create_discount(self, *, pct: int, expires_at: datetime) -> str:
        raise EffectsUnavailableError("Shopify connection is not configured")

    async def reply_to_review(self, review: Review, reply: str) -> None:
        raise EffectsUnavailableError("No review platform is connected")

    async def set_variant_price(
        self, product_shopify_id: int, variant: Variant, price_minor: int
    ) -> None:
        raise EffectsUnavailableError("Shopify connection is not configured")


HOLD_QUERY = """
query OrderFulfillment($id: ID!) {
  order(id: $id) { fulfillmentOrders(first: 20) { nodes { id status } } }
}
"""
HOLD_MUTATION = """
mutation Hold($id: ID!, $hold: FulfillmentOrderHoldInput!) {
  fulfillmentOrderHold(id: $id, fulfillmentHold: $hold) { userErrors { field message } }
}
"""
TAG_MUTATION = """
mutation Tag($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""
PRICE_MUTATION = """
mutation Price($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    userErrors { field message }
  }
}
"""
DISCOUNT_MUTATION = """
mutation Discount($input: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $input) {
    codeDiscountNode { id }
    userErrors { field message }
  }
}
"""


class LiveEffects:
    """Real side effects: Shopify Admin GraphQL plus transactional email via Resend."""

    def __init__(self, shop: Shop, redis: Redis) -> None:
        self.shop = shop
        self.redis = redis

    def _client(self) -> ShopifyClient:
        if self.shop.access_token_encrypted is None:
            raise EffectsUnavailableError("Shopify connection is not configured")
        return ShopifyClient(
            self.shop.domain, decrypt_secret(self.shop.access_token_encrypted), self.redis
        )

    async def hold_order(self, order: Order, reason: str) -> None:
        order_gid = f"gid://shopify/Order/{order.shopify_id}"
        async with self._client() as client:
            data = await client.query(HOLD_QUERY, {"id": order_gid})
            nodes = ((data.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes", [])
            for node in nodes:
                if node.get("status") in ("OPEN", "SCHEDULED"):
                    result = await client.query(
                        HOLD_MUTATION,
                        {
                            "id": node["id"],
                            "hold": {"reason": "HIGH_RISK_OF_FRAUD", "reasonNotes": reason[:255]},
                        },
                    )
                    user_errors(result, "fulfillmentOrderHold")
            result = await client.query(TAG_MUTATION, {"id": order_gid, "tags": ["storeops-held"]})
            user_errors(result, "tagsAdd")

    async def send_email(self, *, to: str, subject: str, text: str, category: str) -> str:
        settings = get_settings()
        if settings.resend_api_key is None:
            raise EffectsUnavailableError("Email provider is not configured")
        async with http_client() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}"},
                json={
                    "from": settings.email_from,
                    "to": [to],
                    "subject": subject,
                    "text": text,
                    "tags": [{"name": "category", "value": category}],
                },
            )
        if response.status_code >= 400:
            raise EffectsUnavailableError(f"Email provider returned HTTP {response.status_code}")
        return str(response.json().get("id", ""))

    async def create_discount(self, *, pct: int, expires_at: datetime) -> str:
        code = f"COMEBACK-{secrets.token_hex(3).upper()}"
        async with self._client() as client:
            result = await client.query(
                DISCOUNT_MUTATION,
                {
                    "input": {
                        "title": f"Cart recovery {code}",
                        "code": code,
                        "startsAt": utcnow().isoformat(),
                        "endsAt": expires_at.isoformat(),
                        "usageLimit": 1,
                        "appliesOncePerCustomer": True,
                        "context": {"all": "ALL"},
                        "customerGets": {
                            "value": {"percentage": round(pct / 100, 4)},
                            "items": {"all": True},
                        },
                    }
                },
            )
            user_errors(result, "discountCodeBasicCreate")
        return code

    async def reply_to_review(self, review: Review, reply: str) -> None:
        raise EffectsUnavailableError("No review platform is connected")

    async def set_variant_price(
        self, product_shopify_id: int, variant: Variant, price_minor: int
    ) -> None:
        async with self._client() as client:
            result = await client.query(
                PRICE_MUTATION,
                {
                    "productId": f"gid://shopify/Product/{product_shopify_id}",
                    "variants": [
                        {
                            "id": f"gid://shopify/ProductVariant/{variant.shopify_id}",
                            "price": f"{price_minor / 100:.2f}",
                        }
                    ],
                },
            )
            user_errors(result, "productVariantsBulkUpdate")


def effects_for(shop: Shop, redis: Redis) -> Effects:
    if shop.mode == ShopMode.DEMO:
        return DemoEffects(shop, redis)
    if shop.access_token_encrypted is not None:
        return LiveEffects(shop, redis)
    return UnconfiguredEffects()
