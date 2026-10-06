import json
import logging
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Auth, DbSession, RedisDep, SettingsDep
from app.core import rate_limit
from app.core.queue import get_queue
from app.models import Shop, User
from app.models.base import utcnow
from app.models.enums import EventSource
from app.pipeline.ingest import ingest_event
from app.shopify import oauth
from app.shopify.client import ShopifyError
from app.shopify.security import is_valid_shop_domain, verify_query, verify_webhook

logger = logging.getLogger("storeops.shopify")

router = APIRouter(tags=["shopify"])

WEBHOOK_RATE_PER_MINUTE = 1200


class InstallRequest(BaseModel):
    shop: str = Field(min_length=3, max_length=255)


@router.get("/api/shopify/status")
async def shopify_status(settings: SettingsDep) -> dict[str, Any]:
    return {"configured": settings.shopify_configured, "app_url": settings.app_url}


def _normalise_domain(raw: str) -> str:
    shop = raw.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if "." not in shop:
        shop = f"{shop}.myshopify.com"
    return shop


@router.post("/api/shopify/install")
async def install(
    body: InstallRequest, auth: Auth, redis: RedisDep, settings: SettingsDep
) -> dict[str, str]:
    if not settings.shopify_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Shopify app is not configured")
    shop = _normalise_domain(body.shop)
    if not is_valid_shop_domain(shop):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter your store's .myshopify.com domain"
        )
    return {"url": await oauth.begin_install(redis, auth.user, shop)}


def _fail(settings: SettingsDep, message: str) -> RedirectResponse:
    return RedirectResponse(
        f"{settings.frontend_origin}/onboarding?error={quote(message)}", status.HTTP_303_SEE_OTHER
    )


@router.get("/api/shopify/callback", include_in_schema=False)
async def callback(
    request: Request, db: DbSession, redis: RedisDep, settings: SettingsDep
) -> RedirectResponse:
    params = dict(request.query_params)
    if not settings.shopify_configured or settings.shopify_api_secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Shopify app is not configured")
    shop = params.get("shop", "")
    if not is_valid_shop_domain(shop) or not verify_query(
        settings.shopify_api_secret.get_secret_value(), params
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Shopify signature")
    try:
        timestamp = int(params.get("timestamp", "0"))
    except ValueError:
        timestamp = 0
    if abs(utcnow().timestamp() - timestamp) > 600:
        return _fail(settings, "The install link is too old. Please try again.")
    try:
        user_id = await oauth.consume_state(redis, params.get("state", ""), shop)
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise oauth.OAuthError("Your account is no longer active.")
        token, scopes = await oauth.exchange_code(shop, params.get("code", ""))
        result = await oauth.complete_install(db, redis, user, shop, token, scopes)
    except oauth.OAuthError as exc:
        return _fail(settings, str(exc))
    except ShopifyError:
        logger.exception("shopify install failed", extra={"shop": shop})
        return _fail(settings, "Shopify returned an error while setting up the app.")
    await get_queue().enqueue("initial_sync", str(result.shop.id), job_id=f"sync:{result.shop.id}")
    return RedirectResponse(
        f"{settings.frontend_origin}/app/{result.shop.id}?installed=1", status.HTTP_303_SEE_OTHER
    )


@router.post("/api/webhooks/shopify", include_in_schema=False)
async def shopify_webhook(
    request: Request,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    x_shopify_hmac_sha256: Annotated[str | None, Header()] = None,
    x_shopify_topic: Annotated[str | None, Header()] = None,
    x_shopify_shop_domain: Annotated[str | None, Header()] = None,
    x_shopify_webhook_id: Annotated[str | None, Header()] = None,
    x_shopify_triggered_at: Annotated[str | None, Header()] = None,
) -> Response:
    """Verify, persist, enqueue, acknowledge. No processing happens on the request path."""
    client = request.client.host if request.client else "unknown"
    if not await rate_limit.hit(redis, f"webhook:{client}", WEBHOOK_RATE_PER_MINUTE):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Slow down")
    if settings.shopify_api_secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Shopify app is not configured")
    body = await request.body()
    if not verify_webhook(
        settings.shopify_api_secret.get_secret_value(), body, x_shopify_hmac_sha256
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid HMAC")
    if not (x_shopify_topic and x_shopify_shop_domain and x_shopify_webhook_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing Shopify headers")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not JSON") from None

    shop = await db.scalar(select(Shop).where(Shop.domain == x_shopify_shop_domain.lower()))
    if shop is None:
        # Nothing stored for this shop (e.g. redact after deletion): acknowledge and drop.
        return Response(status_code=status.HTTP_200_OK)
    triggered_at = None
    if x_shopify_triggered_at:
        try:
            triggered_at = datetime.fromisoformat(x_shopify_triggered_at.replace("Z", "+00:00"))
        except ValueError:
            triggered_at = None
    await ingest_event(
        db,
        get_queue(),
        shop_id=shop.id,
        topic=x_shopify_topic,
        webhook_id=x_shopify_webhook_id,
        payload=payload,
        source=EventSource.SHOPIFY,
        triggered_at=triggered_at,
    )
    return Response(status_code=status.HTTP_200_OK)
