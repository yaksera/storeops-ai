"""Fake Shopify (OAuth + Admin GraphQL) shared by the Shopify, billing and privacy tests."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.shopify import client as shopify_client
from app.shopify.security import webhook_hmac

SECRET = "shpss_test_secret"
SHOP = "acme-outdoors.myshopify.com"


def gql_order(order_id: int = 4001, updated: str = "2026-10-05T10:00:00Z") -> dict[str, Any]:
    return {
        "id": f"gid://shopify/Order/{order_id}",
        "name": "#1001",
        "email": "pat@customers.example",
        "createdAt": "2026-10-05T09:00:00Z",
        "processedAt": "2026-10-05T09:00:00Z",
        "updatedAt": updated,
        "cancelledAt": None,
        "currencyCode": "USD",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "sourceName": "web",
        "subtotalPriceSet": {"shopMoney": {"amount": "120.00"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "0.00"}},
        "totalPriceSet": {"shopMoney": {"amount": "120.00"}},
        "customer": {
            "id": "gid://shopify/Customer/77",
            "email": "pat@customers.example",
            "firstName": "Pat",
            "numberOfOrders": "3",
            "amountSpent": {"amount": "360.00"},
            "defaultAddress": {"countryCodeV2": "US"},
        },
        "billingAddress": {"countryCodeV2": "US"},
        "shippingAddress": {"countryCodeV2": "US"},
        "lineItems": {
            "nodes": [
                {
                    "id": "gid://shopify/LineItem/9",
                    "title": "Trail Mug",
                    "sku": "MUG-1",
                    "quantity": 2,
                    "variant": {"id": "gid://shopify/ProductVariant/501"},
                    "originalUnitPriceSet": {"shopMoney": {"amount": "60.00"}},
                }
            ]
        },
    }


GQL_PRODUCT = {
    "id": "gid://shopify/Product/300",
    "title": "Trail Mug",
    "handle": "trail-mug",
    "vendor": "Acme",
    "productType": "Kitchen",
    "status": "ACTIVE",
    "updatedAt": "2026-10-05T08:00:00Z",
    "featuredImage": None,
    "variants": {
        "nodes": [
            {
                "id": "gid://shopify/ProductVariant/501",
                "sku": "MUG-1",
                "title": "Default",
                "price": "60.00",
                "inventoryQuantity": 40,
                "inventoryItem": {"id": "gid://shopify/InventoryItem/601"},
            }
        ]
    },
}


class FakeShopify:
    """Minimal stand-in for the Shopify OAuth and Admin GraphQL endpoints."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.graphql: list[dict[str, Any]] = []
        self.throttle_first = False
        self.reject_token = False
        self.active_subscriptions: list[dict[str, Any]] = []
        self.rejected_topics: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if url == "https://api.resend.com/emails":
            return httpx.Response(200, json={"id": "email-123"})
        if url.endswith("/admin/oauth/access_token"):
            body = json.loads(request.content)
            assert body["client_secret"] == SECRET
            return httpx.Response(
                200, json={"access_token": "shpat_live_token", "scope": "read_orders"}
            )
        if self.reject_token:
            return httpx.Response(401, json={"errors": "Invalid API key or access token"})
        body = json.loads(request.content)
        self.graphql.append(body)
        query: str = body["query"]
        if self.throttle_first:
            self.throttle_first = False
            return httpx.Response(
                200,
                json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]},
            )
        cost = {
            "throttleStatus": {
                "maximumAvailable": 2000,
                "currentlyAvailable": 1900,
                "restoreRate": 100,
            }
        }
        if "shop {" in query:
            data: dict[str, Any] = {
                "shop": {
                    "name": "Acme Outdoors",
                    "currencyCode": "CAD",
                    "ianaTimezone": "America/Toronto",
                }
            }
        elif "webhookSubscriptionCreate" in query:
            rejected = body["variables"]["topic"] in self.rejected_topics
            data = {
                "webhookSubscriptionCreate": {
                    "webhookSubscription": None if rejected else {"id": "gid://1"},
                    "userErrors": [
                        {"field": ["topic"], "message": "Protected customer data access required"}
                    ]
                    if rejected
                    else [],
                }
            }
        elif "products(" in query:
            data = {
                "products": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [GQL_PRODUCT],
                }
            }
        elif "orders(" in query:
            data = {
                "orders": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [gql_order()],
                }
            }
        elif "fulfillmentOrders" in query:
            data = {
                "order": {
                    "fulfillmentOrders": {
                        "nodes": [
                            {"id": "gid://FO/1", "status": "OPEN"},
                            {"id": "gid://FO/2", "status": "CLOSED"},
                        ]
                    }
                }
            }
        elif "fulfillmentOrderHold" in query:
            data = {"fulfillmentOrderHold": {"userErrors": []}}
        elif "tagsAdd" in query:
            data = {"tagsAdd": {"userErrors": []}}
        elif "appSubscriptionCreate" in query:
            data = {
                "appSubscriptionCreate": {
                    "confirmationUrl": f"https://{SHOP}/admin/charges/confirm?plan="
                    + body["variables"]["name"],
                    "appSubscription": {
                        "id": "gid://shopify/AppSubscription/1",
                        "status": "PENDING",
                    },
                    "userErrors": [],
                }
            }
        elif "currentAppInstallation" in query:
            data = {"currentAppInstallation": {"activeSubscriptions": self.active_subscriptions}}
        elif "appSubscriptionCancel" in query:
            self.active_subscriptions = []
            data = {"appSubscriptionCancel": {"appSubscription": None, "userErrors": []}}
        elif "discountCodeBasicCreate" in query:
            data = {
                "discountCodeBasicCreate": {
                    "codeDiscountNode": {"id": "gid://D/1"},
                    "userErrors": [],
                }
            }
        else:
            raise AssertionError(f"unexpected query {query[:60]}")
        return httpx.Response(200, json={"data": data, "extensions": {"cost": cost}})


@pytest.fixture
def fake_shopify(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeShopify]:
    fake = FakeShopify()
    settings = get_settings()
    monkeypatch.setattr(settings, "shopify_api_key", "test-client-id")
    monkeypatch.setattr(settings, "shopify_api_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "public_app_url", "https://storeops.example")
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test"))
    shopify_client.set_transport(httpx.MockTransport(fake.handler))

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.shopify.client.asyncio.sleep", no_sleep)
    yield fake
    shopify_client.set_transport(None)


def signed(body: bytes) -> dict[str, str]:
    return {"x-shopify-hmac-sha256": webhook_hmac(SECRET, body)}
