"""Pull data from Shopify: first sync after install and nightly reconciliation.

GraphQL results are converted to the same payload shape as REST webhooks and fed through the
normal ingest pipeline, so missed webhooks are repaired by exactly the same code paths.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.queue import JobQueue
from app.models import Shop
from app.models.base import utcnow
from app.models.enums import EventSource, ShopStatus
from app.pipeline.ingest import ingest_event
from app.shopify.client import ShopifyClient, gid_to_int

logger = logging.getLogger("storeops.shopify.sync")

PRODUCTS_QUERY = """
query Products($cursor: String) {
  products(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title handle vendor productType status updatedAt
      featuredImage { url }
      variants(first: 100) {
        nodes { id sku title price inventoryQuantity inventoryItem { id } }
      }
    }
  }
}
"""

ORDERS_QUERY = """
query Orders($cursor: String, $query: String) {
  orders(first: 50, after: $cursor, query: $query, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name email createdAt processedAt updatedAt cancelledAt
      currencyCode displayFinancialStatus displayFulfillmentStatus sourceName
      subtotalPriceSet { shopMoney { amount } }
      totalDiscountsSet { shopMoney { amount } }
      totalPriceSet { shopMoney { amount } }
      customer {
        id email firstName numberOfOrders
        amountSpent { amount }
        defaultAddress { countryCodeV2 }
      }
      billingAddress { countryCodeV2 }
      shippingAddress { countryCodeV2 }
      lineItems(first: 50) {
        nodes {
          id title sku quantity
          variant { id }
          originalUnitPriceSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""

SHOP_QUERY = "query { shop { name currencyCode ianaTimezone } }"


def _money(node: dict[str, Any] | None) -> str | None:
    return ((node or {}).get("shopMoney") or {}).get("amount")


def _country(node: dict[str, Any] | None) -> dict[str, Any] | None:
    code = (node or {}).get("countryCodeV2")
    return {"country_code": code} if code else None


def product_payload(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": gid_to_int(node["id"]),
        "title": node.get("title"),
        "handle": node.get("handle"),
        "vendor": node.get("vendor"),
        "product_type": node.get("productType"),
        "status": str(node.get("status") or "ACTIVE").lower(),
        "updated_at": node.get("updatedAt"),
        "image": {"src": node["featuredImage"]["url"]} if node.get("featuredImage") else None,
        "variants": [
            {
                "id": gid_to_int(v["id"]),
                "sku": v.get("sku"),
                "title": v.get("title"),
                "price": v.get("price"),
                "inventory_quantity": v.get("inventoryQuantity"),
                "inventory_item_id": gid_to_int((v.get("inventoryItem") or {}).get("id")),
            }
            for v in (node.get("variants") or {}).get("nodes", [])
        ],
    }


def order_payload(node: dict[str, Any]) -> dict[str, Any]:
    customer = node.get("customer")
    fulfillment = str(node.get("displayFulfillmentStatus") or "").lower()
    return {
        "id": gid_to_int(node["id"]),
        "name": node.get("name"),
        "email": node.get("email"),
        "created_at": node.get("createdAt"),
        "processed_at": node.get("processedAt") or node.get("createdAt"),
        "updated_at": node.get("updatedAt"),
        "cancelled_at": node.get("cancelledAt"),
        "currency": node.get("currencyCode"),
        "financial_status": str(node.get("displayFinancialStatus") or "").lower() or None,
        "fulfillment_status": None if fulfillment in ("", "unfulfilled") else fulfillment,
        "source_name": node.get("sourceName"),
        "subtotal_price": _money(node.get("subtotalPriceSet")),
        "total_discounts": _money(node.get("totalDiscountsSet")),
        "total_price": _money(node.get("totalPriceSet")),
        "customer": (
            {
                "id": gid_to_int(customer["id"]),
                "email": customer.get("email"),
                "first_name": customer.get("firstName"),
                "orders_count": int(customer.get("numberOfOrders") or 0),
                "total_spent": (customer.get("amountSpent") or {}).get("amount"),
                "default_address": _country(customer.get("defaultAddress")),
            }
            if customer
            else None
        ),
        "billing_address": _country(node.get("billingAddress")),
        "shipping_address": _country(node.get("shippingAddress")),
        "line_items": [
            {
                "id": gid_to_int(li["id"]),
                "title": li.get("title"),
                "sku": li.get("sku"),
                "quantity": li.get("quantity"),
                "variant_id": gid_to_int((li.get("variant") or {}).get("id")),
                "price": _money(li.get("originalUnitPriceSet")),
            }
            for li in (node.get("lineItems") or {}).get("nodes", [])
        ],
    }


def client_for(shop: Shop, redis: Redis) -> ShopifyClient:
    if shop.access_token_encrypted is None:
        raise RuntimeError("shop has no access token")
    return ShopifyClient(shop.domain, decrypt_secret(shop.access_token_encrypted), redis)


async def _paginate(
    client: ShopifyClient, query: str, root: str, variables: dict[str, Any]
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = await client.query(query, {**variables, "cursor": cursor}, cost=120)
        page = data[root]
        nodes.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


async def _set_progress(redis: Redis, shop_id: uuid.UUID, **state: Any) -> None:
    await redis.set(f"shop:{shop_id}:sync", json.dumps(state, default=str), ex=86_400)


async def sync_shop(
    db: AsyncSession,
    redis: Redis,
    queue: JobQueue,
    shop: Shop,
    *,
    since: datetime,
    source: EventSource,
) -> dict[str, int]:
    """Fetch products and orders updated since `since` and push them through ingest."""
    if shop.status != ShopStatus.ACTIVE:
        return {"products": 0, "orders": 0}
    await _set_progress(redis, shop.id, state="syncing", products=0, orders=0)
    async with client_for(shop, redis) as client:
        products = await _paginate(client, PRODUCTS_QUERY, "products", {})
        for node in products:
            payload = product_payload(node)
            await ingest_event(
                db,
                queue,
                shop_id=shop.id,
                topic="products/update",
                webhook_id=f"{source.value}:product:{payload['id']}:{payload['updated_at']}",
                payload=payload,
                source=source,
            )
        await _set_progress(redis, shop.id, state="syncing", products=len(products), orders=0)
        orders = await _paginate(
            client, ORDERS_QUERY, "orders", {"query": f"updated_at:>='{since.isoformat()}'"}
        )
        # Oldest first so customers' order counts build up in order.
        for node in reversed(orders):
            payload = order_payload(node)
            await ingest_event(
                db,
                queue,
                shop_id=shop.id,
                topic="orders/updated",
                webhook_id=f"{source.value}:order:{payload['id']}:{payload['updated_at']}",
                payload=payload,
                source=source,
            )
    result = {"products": len(products), "orders": len(orders)}
    await _set_progress(redis, shop.id, state="done", finished_at=utcnow(), **result)
    logger.info("shop synced", extra={"shop": shop.domain, "source": source.value, **result})
    return result


def reconciliation_window(now: datetime | None = None) -> datetime:
    # Overlaps the previous nightly run so nothing slips through at the boundary.
    return (now or utcnow()) - timedelta(hours=26)
