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

from app.models import Order, Shop
from app.models.base import utcnow
from app.models.enums import ShopMode


class EffectsUnavailableError(Exception):
    pass


class Effects(Protocol):
    async def hold_order(self, order: Order, reason: str) -> None: ...

    async def send_email(self, *, to: str, subject: str, text: str, category: str) -> str: ...

    async def create_discount(self, *, pct: int, expires_at: datetime) -> str: ...


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


class UnconfiguredEffects:
    """Live stores until the Shopify and email integrations are connected."""

    async def hold_order(self, order: Order, reason: str) -> None:
        raise EffectsUnavailableError("Shopify connection is not configured")

    async def send_email(self, *, to: str, subject: str, text: str, category: str) -> str:
        raise EffectsUnavailableError("Email provider is not configured")

    async def create_discount(self, *, pct: int, expires_at: datetime) -> str:
        raise EffectsUnavailableError("Shopify connection is not configured")


def effects_for(shop: Shop, redis: Redis) -> Effects:
    if shop.mode == ShopMode.DEMO:
        return DemoEffects(shop, redis)
    return UnconfiguredEffects()
