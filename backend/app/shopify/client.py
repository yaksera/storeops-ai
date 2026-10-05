"""Shopify Admin GraphQL client with cost-based rate limiting.

Shopify meters GraphQL by query cost with a leaky bucket per shop and reports its state in
`extensions.cost.throttleStatus`. We mirror that bucket in Redis (shared by every API and worker
process), wait before sending a query that wouldn't fit, and back off with jitter on THROTTLED
responses and transient errors. The access token is never logged.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx
from redis.asyncio import Redis

from app.core.config import get_settings

logger = logging.getLogger("storeops.shopify")

DEFAULT_COST = 50
MAX_ATTEMPTS = 5

_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Route all Shopify HTTP traffic through a custom transport (tests)."""
    global _transport
    _transport = transport


def http_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=20.0, **kwargs)


class ShopifyError(Exception):
    pass


class ShopifyAuthError(ShopifyError):
    """The token was revoked (the app was probably uninstalled)."""


def gid_to_int(gid: str | int | None) -> int | None:
    if gid is None:
        return None
    if isinstance(gid, int):
        return gid
    return int(str(gid).rsplit("/", 1)[-1].split("?")[0])


class RateLimiter:
    def __init__(self, redis: Redis, shop_domain: str) -> None:
        self.redis = redis
        self.key = f"shopify:{shop_domain}:bucket"

    async def wait_for(self, cost: int) -> float:
        """Sleep until `cost` points are likely available. Returns the seconds waited."""
        raw = await self.redis.get(self.key)
        if raw is None:
            return 0.0
        state = json.loads(raw)
        elapsed = time.time() - state["at"]
        available = min(state["maximum"], state["available"] + state["restore_rate"] * elapsed)
        if available >= cost:
            return 0.0
        delay = float((cost - available) / max(state["restore_rate"], 1.0))
        await asyncio.sleep(delay)
        return delay

    async def record(self, throttle: dict[str, Any]) -> None:
        state = {
            "maximum": float(throttle.get("maximumAvailable", 1000)),
            "available": float(throttle.get("currentlyAvailable", 1000)),
            "restore_rate": float(throttle.get("restoreRate", 50)),
            "at": time.time(),
        }
        await self.redis.set(self.key, json.dumps(state), ex=300)


class ShopifyClient:
    def __init__(self, shop_domain: str, access_token: str, redis: Redis) -> None:
        settings = get_settings()
        self.shop_domain = shop_domain
        self.limiter = RateLimiter(redis, shop_domain)
        self._url = f"https://{shop_domain}/admin/api/{settings.shopify_api_version}/graphql.json"
        self._http = http_client(
            headers={"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "ShopifyClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def query(
        self, query: str, variables: dict[str, Any] | None = None, *, cost: int = DEFAULT_COST
    ) -> dict[str, Any]:
        last_error = "unknown error"
        for attempt in range(MAX_ATTEMPTS):
            await self.limiter.wait_for(cost)
            try:
                response = await self._http.post(
                    self._url, json={"query": query, "variables": variables or {}}
                )
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}"
                await self._backoff(attempt)
                continue
            if response.status_code in (401, 403):
                raise ShopifyAuthError(
                    f"Shopify rejected the access token ({response.status_code})"
                )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                retry_after = float(response.headers.get("Retry-After", 0) or 0)
                await self._backoff(attempt, minimum=retry_after)
                continue
            if response.status_code >= 400:
                raise ShopifyError(f"Shopify returned HTTP {response.status_code}")

            body = response.json()
            throttle = (body.get("extensions") or {}).get("cost", {}).get("throttleStatus")
            if throttle:
                await self.limiter.record(throttle)
            errors = body.get("errors") or []
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
                last_error = "THROTTLED"
                await self._backoff(attempt)
                continue
            if errors:
                raise ShopifyError("; ".join(str(e.get("message", e)) for e in errors)[:500])
            data: dict[str, Any] = body.get("data") or {}
            return data
        raise ShopifyError(f"Shopify request failed after {MAX_ATTEMPTS} attempts: {last_error}")

    async def _backoff(self, attempt: int, minimum: float = 0.0) -> None:
        delay = max(minimum, min(8.0, 0.5 * 2**attempt)) + random.uniform(0, 0.3)  # noqa: S311
        logger.warning(
            "shopify retry", extra={"shop": self.shop_domain, "attempt": attempt, "delay": delay}
        )
        await asyncio.sleep(delay)


def user_errors(payload: dict[str, Any], mutation: str) -> None:
    errors = (payload.get(mutation) or {}).get("userErrors") or []
    if errors:
        raise ShopifyError("; ".join(e.get("message", "error") for e in errors)[:500])
