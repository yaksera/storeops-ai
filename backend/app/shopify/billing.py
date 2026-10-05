"""Shopify Billing API: recurring app subscriptions for the Growth and Pro plans."""

from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings
from app.models import Shop
from app.models.enums import Plan
from app.services.billing import PLANS
from app.shopify.client import ShopifyError, user_errors
from app.shopify.sync import client_for

CREATE_MUTATION = """
mutation Subscribe(
  $name: String!
  $returnUrl: URL!
  $test: Boolean
  $lineItems: [AppSubscriptionLineItemInput!]!
) {
  appSubscriptionCreate(name: $name, returnUrl: $returnUrl, test: $test, lineItems: $lineItems) {
    confirmationUrl
    appSubscription { id status }
    userErrors { field message }
  }
}
"""

CANCEL_MUTATION = """
mutation Cancel($id: ID!) {
  appSubscriptionCancel(id: $id) { appSubscription { id status } userErrors { field message } }
}
"""

ACTIVE_QUERY = """
query { currentAppInstallation { activeSubscriptions { id name status currentPeriodEnd test } } }
"""


def plan_from_name(name: str | None) -> Plan | None:
    for plan, spec in PLANS.items():
        if name and name.lower().startswith(f"storeops {spec.name.lower()}"):
            return plan
    return None


async def create_subscription(shop: Shop, redis: Redis, plan: Plan, return_url: str) -> str:
    spec = PLANS[plan]
    async with client_for(shop, redis) as client:
        result = await client.query(
            CREATE_MUTATION,
            {
                "name": f"StoreOps {spec.name}",
                "returnUrl": return_url,
                "test": not get_settings().is_production,
                "lineItems": [
                    {
                        "plan": {
                            "appRecurringPricingDetails": {
                                "price": {"amount": str(spec.price_usd), "currencyCode": "USD"},
                                "interval": "EVERY_30_DAYS",
                            }
                        }
                    }
                ],
            },
        )
        user_errors(result, "appSubscriptionCreate")
    url = (result.get("appSubscriptionCreate") or {}).get("confirmationUrl")
    if not url:
        raise ShopifyError("Shopify did not return a confirmation URL")
    return str(url)


async def active_subscriptions(shop: Shop, redis: Redis) -> list[dict[str, Any]]:
    async with client_for(shop, redis) as client:
        data = await client.query(ACTIVE_QUERY)
    return list((data.get("currentAppInstallation") or {}).get("activeSubscriptions") or [])


async def cancel_subscription(shop: Shop, redis: Redis, subscription_gid: str) -> None:
    async with client_for(shop, redis) as client:
        result = await client.query(CANCEL_MUTATION, {"id": subscription_gid})
        user_errors(result, "appSubscriptionCancel")
