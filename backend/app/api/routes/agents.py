import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from sqlalchemy import select

from app.agents.activity import AGENT_LABELS
from app.agents.base import AgentContext
from app.agents.runner import REGISTRY
from app.api.deps import DbSession, RedisDep, ShopAdmin, ShopViewer
from app.models import AgentConfig, Checkout, Customer, Order, Product, ShopSettings, Variant
from app.models.base import utcnow
from app.models.enums import ActorType, AgentName, Autonomy, CheckoutStatus
from app.pipeline.normalize import Change, order_summary
from app.services import audit

router = APIRouter(prefix="/api/shops/{shop_id}/agents", tags=["agents"])

DESCRIPTIONS: dict[AgentName, str] = {
    AgentName.ORCHESTRATOR: "Routes events to agents, enforces limits and writes the audit log.",
    AgentName.FRAUD_GUARD: "Scores every new order and holds risky ones before they ship.",
    AgentName.INVENTORY_PLANNER: "Forecasts stockouts and drafts supplier purchase orders.",
    AgentName.CART_RECOVERY: "Follows up on abandoned checkouts with personal emails.",
    AgentName.SUPPORT: "Answers order-status, returns and sizing questions.",
    AgentName.REVIEW_REPUTATION: "Triages reviews and drafts replies.",
    AgentName.REVENUE_ANALYST: "Flags sales anomalies and explains the likely cause.",
    AgentName.PRICING_ADVISOR: "Suggests price changes from stock, sell-through and margin.",
}


class CartRecoverySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    abandon_after_minutes: int = Field(ge=1, le=10_080)
    reminder_interval_minutes: int = Field(ge=1, le=10_080)
    discount_pct: int = Field(ge=0, le=10)


class InventorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_email: EmailStr | None = None


class PricingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    margin_floor_pct: int = Field(ge=0, le=500)


SETTINGS_SCHEMAS: dict[AgentName, type[BaseModel]] = {
    AgentName.PRICING_ADVISOR: PricingSettings,
    AgentName.CART_RECOVERY: CartRecoverySettings,
    AgentName.INVENTORY_PLANNER: InventorySettings,
}


class AgentConfigOut(BaseModel):
    agent: AgentName
    label: str
    description: str
    available: bool
    enabled: bool
    autonomy: Autonomy
    daily_action_cap: int
    model: str | None
    tone: str
    settings: dict[str, Any]


class AgentConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    autonomy: Autonomy | None = None
    daily_action_cap: int | None = Field(default=None, ge=0, le=10_000)
    model: str | None = Field(default=None, max_length=128)
    tone: str | None = Field(default=None, max_length=64)
    settings: dict[str, Any] | None = None


def _out(config: AgentConfig) -> AgentConfigOut:
    return AgentConfigOut(
        agent=config.agent,
        label=AGENT_LABELS[config.agent],
        description=DESCRIPTIONS[config.agent],
        available=config.agent in REGISTRY or config.agent == AgentName.ORCHESTRATOR,
        enabled=config.enabled,
        autonomy=config.autonomy,
        daily_action_cap=config.daily_action_cap,
        model=config.model,
        tone=config.tone,
        settings=config.settings,
    )


async def _config(db: DbSession, shop_id: uuid.UUID, agent: AgentName) -> AgentConfig:
    config = await db.scalar(
        select(AgentConfig).where(AgentConfig.shop_id == shop_id, AgentConfig.agent == agent)
    )
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return config


@router.get("")
async def list_agents(ctx: ShopViewer, db: DbSession) -> list[AgentConfigOut]:
    configs = (
        await db.scalars(select(AgentConfig).where(AgentConfig.shop_id == ctx.shop.id))
    ).all()
    order = list(AgentName)
    return [_out(c) for c in sorted(configs, key=lambda c: order.index(c.agent))]


@router.patch("/{agent}")
async def update_agent(
    agent: AgentName, body: AgentConfigUpdate, ctx: ShopAdmin, db: DbSession
) -> AgentConfigOut:
    config = await _config(db, ctx.shop.id, agent)
    changes = body.model_dump(exclude_unset=True)
    if agent == AgentName.PRICING_ADVISOR and changes.get("autonomy") == Autonomy.AUTO:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Pricing Advisor only runs in suggest mode"
        )
    if "settings" in changes:
        schema = SETTINGS_SCHEMAS.get(agent)
        if schema is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This agent has no settings")
        merged = {**config.settings, **(changes["settings"] or {})}
        try:
            changes["settings"] = schema.model_validate(merged).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
            ) from None
    before = {key: getattr(config, key) for key in changes}
    for key, value in changes.items():
        setattr(config, key, value)
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="agent.updated",
        target_type="agent",
        target_id=agent.value,
        details={
            "changes": {k: {"from": str(before[k]), "to": str(v)} for k, v in changes.items()}
        },
    )
    await db.commit()
    return _out(config)


async def _sample_change(db: DbSession, ctx: ShopAdmin, agent: AgentName) -> Change | None:
    shop_id = ctx.shop.id
    if agent == AgentName.FRAUD_GUARD:
        order = await db.scalar(
            select(Order)
            .where(Order.shop_id == shop_id)
            .order_by(Order.processed_at.desc())
            .limit(1)
        )
        return Change("order.created", order_summary(order, None, 0)) if order else None
    if agent == AgentName.INVENTORY_PLANNER:
        row = (
            await db.execute(
                select(Variant, Product)
                .join(Product, Product.id == Variant.product_id)
                .where(Variant.shop_id == shop_id)
                .order_by(Variant.inventory_quantity)
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        variant, product = row
        return Change(
            "inventory.changed",
            {"variant_id": str(variant.id), "product": product.title, "sku": variant.sku},
        )
    if agent == AgentName.CART_RECOVERY:
        checkout = await db.scalar(
            select(Checkout)
            .join(Customer, Customer.id == Checkout.customer_id)
            .where(
                Checkout.shop_id == shop_id,
                Checkout.status.in_([CheckoutStatus.OPEN, CheckoutStatus.ABANDONED]),
                Checkout.email.is_not(None),
                Customer.accepts_marketing.is_(True),
            )
            .order_by(Checkout.shopify_created_at.desc())
            .limit(1)
        )
        return Change("checkout.abandoned", {"id": str(checkout.id)}) if checkout else None
    return None


@router.post("/{agent}/test")
async def test_agent(
    agent: AgentName, ctx: ShopAdmin, db: DbSession, redis: RedisDep
) -> dict[str, Any]:
    """Run the agent's decide() on a recent real event without saving or executing anything."""
    implementation = REGISTRY.get(agent)
    if implementation is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This agent isn't available yet")
    change = await _sample_change(db, ctx, agent)
    if change is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No suitable sample event in this store yet")
    config = await _config(db, ctx.shop.id, agent)
    settings = (
        await db.execute(select(ShopSettings).where(ShopSettings.shop_id == ctx.shop.id))
    ).scalar_one()
    agent_ctx = AgentContext(
        db=db, redis=redis, shop=ctx.shop, settings=settings, config=config, now=utcnow()
    )
    try:
        decision = await implementation.decide(agent_ctx, change)
    finally:
        await db.rollback()
    if decision is None:
        return {
            "event": {"kind": change.kind, "data": change.data},
            "output": None,
            "proposals": [],
        }
    return {
        "event": {"kind": change.kind, "data": change.data},
        "output": decision.output,
        "proposals": [
            {
                "action_type": p.action_type,
                "title": p.title,
                "summary": p.summary,
                "rationale": p.rationale,
                "risk_level": p.risk_level.value,
                "preview": p.preview,
            }
            for p in decision.proposals
        ],
    }
