"""OAuth install flow and post-install setup (shop details, webhook subscriptions)."""

import json
import secrets
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from app.models import Membership, Shop, User
from app.models.base import utcnow
from app.models.enums import ActorType, Role, ShopMode, ShopStatus
from app.services import audit
from app.services.shops import create_shop
from app.shopify.client import ShopifyClient, ShopifyError, http_client, user_errors
from app.shopify.sync import SHOP_QUERY

STATE_TTL_SECONDS = 600

WEBHOOK_TOPICS = (
    "ORDERS_CREATE",
    "ORDERS_UPDATED",
    "ORDERS_CANCELLED",
    "REFUNDS_CREATE",
    "PRODUCTS_UPDATE",
    "INVENTORY_LEVELS_UPDATE",
    "CHECKOUTS_CREATE",
    "CHECKOUTS_UPDATE",
    "FULFILLMENTS_CREATE",
    "APP_UNINSTALLED",
)

WEBHOOK_MUTATION = """
mutation Subscribe($topic: WebhookSubscriptionTopic!, $url: URL!) {
  webhookSubscriptionCreate(
    topic: $topic,
    webhookSubscription: { callbackUrl: $url, format: JSON }
  ) {
    webhookSubscription { id }
    userErrors { field message }
  }
}
"""


class OAuthError(Exception):
    pass


def _state_key(state: str) -> str:
    return f"shopify:oauth:{state}"


async def begin_install(redis: Redis, user: User, shop_domain: str) -> str:
    settings = get_settings()
    state = secrets.token_urlsafe(24)
    await redis.set(
        _state_key(state),
        json.dumps({"user_id": str(user.id), "shop": shop_domain}),
        ex=STATE_TTL_SECONDS,
    )
    query = urlencode(
        {
            "client_id": settings.shopify_api_key,
            "scope": settings.shopify_scopes,
            "redirect_uri": f"{settings.app_url}/api/shopify/callback",
            "state": state,
        }
    )
    return f"https://{shop_domain}/admin/oauth/authorize?{query}"


async def consume_state(redis: Redis, state: str, shop_domain: str) -> uuid.UUID:
    raw = await redis.getdel(_state_key(state))
    if raw is None:
        raise OAuthError("Install link expired. Please start again.")
    data = json.loads(raw)
    if data["shop"] != shop_domain:
        raise OAuthError("Shop does not match the install request.")
    return uuid.UUID(data["user_id"])


async def exchange_code(shop_domain: str, code: str) -> tuple[str, str]:
    settings = get_settings()
    assert settings.shopify_api_secret is not None
    async with http_client() as client:
        response = await client.post(
            f"https://{shop_domain}/admin/oauth/access_token",
            json={
                "client_id": settings.shopify_api_key,
                "client_secret": settings.shopify_api_secret.get_secret_value(),
                "code": code,
            },
        )
    if response.status_code != 200:
        raise OAuthError("Shopify did not accept the authorization code.")
    body = response.json()
    return str(body["access_token"]), str(body.get("scope", ""))


@dataclass(frozen=True, slots=True)
class InstallResult:
    shop: Shop
    created: bool


async def complete_install(
    db: AsyncSession, redis: Redis, user: User, shop_domain: str, token: str, scopes: str
) -> InstallResult:
    async with ShopifyClient(shop_domain, token, redis) as client:
        info = (await client.query(SHOP_QUERY))["shop"]
        callback = f"{get_settings().app_url}/api/webhooks/shopify"
        for topic in WEBHOOK_TOPICS:
            result = await client.query(WEBHOOK_MUTATION, {"topic": topic, "url": callback})
            try:
                user_errors(result, "webhookSubscriptionCreate")
            except ShopifyError as exc:
                # Re-installs hit "address already taken"; anything else is a real failure.
                if "taken" not in str(exc).lower():
                    raise

    shop = await db.scalar(select(Shop).where(Shop.domain == shop_domain))
    created = shop is None
    if shop is None:
        shop = await create_shop(
            db,
            owner=user,
            name=info["name"],
            domain=shop_domain,
            mode=ShopMode.LIVE,
            currency=info["currencyCode"],
            timezone=info["ianaTimezone"],
        )
    else:
        membership = await db.scalar(
            select(Membership).where(Membership.shop_id == shop.id, Membership.user_id == user.id)
        )
        if membership is None:
            if shop.status == ShopStatus.ACTIVE:
                raise OAuthError("This store is already connected to another account.")
            db.add(Membership(shop_id=shop.id, user_id=user.id, role=Role.OWNER))
        shop.name = info["name"]
        shop.currency = info["currencyCode"]
        shop.timezone = info["ianaTimezone"]
        shop.status = ShopStatus.ACTIVE
        shop.uninstalled_at = None
        shop.data_deletion_due_at = None
    shop.access_token_encrypted = encrypt_secret(token)
    shop.scopes = scopes
    shop.installed_at = utcnow()
    audit.record(
        db,
        shop_id=shop.id,
        actor_type=ActorType.USER,
        actor_id=user.id,
        action="shopify.installed",
        target_type="shop",
        target_id=shop.id,
        details={"scopes": scopes.split(","), "reinstall": not created},
    )
    await db.commit()
    return InstallResult(shop=shop, created=created)
