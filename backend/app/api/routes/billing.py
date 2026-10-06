import logging
import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DbSession, RedisDep, SettingsDep, ShopOwner, ShopViewer
from app.core import signing
from app.models import Shop, Subscription
from app.models.base import utcnow
from app.models.enums import ActorType, BillingProvider, Plan, ShopMode
from app.services import billing
from app.shopify import billing as shopify_billing
from app.shopify.client import ShopifyError

logger = logging.getLogger("storeops.billing")

router = APIRouter(tags=["billing"])

RETURN_TOKEN_SECONDS = 3600


class SubscribeBody(BaseModel):
    plan: Plan


@router.get("/api/shops/{shop_id}/billing")
async def billing_overview(ctx: ShopViewer, db: DbSession) -> dict[str, Any]:
    now = utcnow()
    spec = billing.spec(ctx.shop)
    used = await billing.usage(db, ctx.shop.id, now)
    subscription = await db.scalar(
        select(Subscription)
        .where(Subscription.shop_id == ctx.shop.id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    return {
        "plan": spec.to_dict(),
        "metered": billing.metered(ctx.shop),
        "usage": {
            "period_start": billing.period_start(now).isoformat(),
            "ai_actions": used[billing.AI_ACTIONS],
            "ai_actions_limit": spec.ai_actions,
            "llm_cost_usd": round(used[billing.LLM_COST] / 1_000_000, 4),
            "llm_budget_usd": str(spec.llm_budget_usd),
        },
        "subscription": (
            {
                "status": subscription.status.value,
                "provider": subscription.provider.value,
                "current_period_end": (
                    subscription.current_period_end.isoformat()
                    if subscription.current_period_end
                    else None
                ),
            }
            if subscription
            else None
        ),
        "plans": [p.to_dict() for p in billing.PLANS.values()],
    }


@router.post("/api/shops/{shop_id}/billing/subscribe")
async def subscribe(
    body: SubscribeBody, ctx: ShopOwner, db: DbSession, redis: RedisDep, settings: SettingsDep
) -> dict[str, Any]:
    shop = ctx.shop
    if body.plan == shop.plan:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Already on the {body.plan.value} plan")
    now = utcnow()
    if shop.mode == ShopMode.DEMO:
        # Demo stores switch instantly so the flow can be explored without being charged.
        await billing.apply_plan(
            db,
            shop,
            body.plan,
            provider=BillingProvider.NONE,
            external_id=None,
            actor_type=ActorType.USER,
            actor_id=ctx.user.id,
            now=now,
        )
        await db.commit()
        return {"plan": body.plan.value, "confirmation_url": None}
    try:
        if body.plan == Plan.FREE:
            for sub in await shopify_billing.active_subscriptions(shop, redis):
                await shopify_billing.cancel_subscription(shop, redis, sub["id"])
            await billing.apply_plan(
                db,
                shop,
                Plan.FREE,
                provider=BillingProvider.SHOPIFY,
                external_id=None,
                actor_type=ActorType.USER,
                actor_id=ctx.user.id,
                now=now,
            )
            await db.commit()
            return {"plan": Plan.FREE.value, "confirmation_url": None}
        token = signing.sign(
            "billing-return",
            {"shop": str(shop.id), "plan": body.plan.value, "user": str(ctx.user.id)},
            max_age_seconds=RETURN_TOKEN_SECONDS,
        )
        return_url = f"{settings.app_url}/api/billing/return?t={token}"
        url = await shopify_billing.create_subscription(shop, redis, body.plan, return_url)
    except ShopifyError as exc:
        logger.warning("billing request failed", extra={"shop": shop.domain, "error": str(exc)})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Shopify billing is unavailable") from None
    return {"plan": shop.plan.value, "confirmation_url": url}


@router.get("/api/billing/return", include_in_schema=False)
async def billing_return(
    t: Annotated[str, Query(max_length=1024)],
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> RedirectResponse:
    """Shopify sends the merchant here after approving (or declining) the charge."""
    try:
        data = signing.unsign("billing-return", t)
    except signing.BadSignatureError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired link") from None
    shop = await db.get(Shop, uuid.UUID(data["shop"]))
    if shop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")
    target = f"{settings.frontend_origin}/app/{shop.id}/billing"
    wanted = Plan(data["plan"])
    try:
        subscriptions = await shopify_billing.active_subscriptions(shop, redis)
    except ShopifyError:
        return RedirectResponse(f"{target}?error={quote('Could not confirm the charge')}", 303)
    active = next(
        (
            s
            for s in subscriptions
            if s.get("status") == "ACTIVE"
            and shopify_billing.plan_from_name(s.get("name")) == wanted
        ),
        None,
    )
    if active is None:
        return RedirectResponse(f"{target}?error={quote('The charge was not approved')}", 303)
    await billing.apply_plan(
        db,
        shop,
        wanted,
        provider=BillingProvider.SHOPIFY,
        external_id=active["id"],
        actor_type=ActorType.USER,
        actor_id=data.get("user"),
        now=utcnow(),
    )
    await db.commit()
    return RedirectResponse(f"{target}?upgraded={wanted.value}", 303)
