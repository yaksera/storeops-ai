"""Plans, usage metering and plan limits."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentConfig, Membership, Notification, Shop, Subscription, UsageCounter
from app.models.enums import (
    ActorType,
    AgentName,
    BillingProvider,
    Plan,
    Role,
    Severity,
    ShopMode,
    ShopStatus,
    SubscriptionStatus,
)
from app.services import audit

AI_ACTIONS = "ai_actions"
LLM_COST = "llm_cost_micro_usd"
ALERT_THRESHOLDS = (0.8, 1.0)


@dataclass(frozen=True, slots=True)
class PlanSpec:
    plan: Plan
    name: str
    price_usd: Decimal
    ai_actions: int | None
    llm_budget_usd: Decimal
    max_live_agents: int | None
    max_live_stores: int | None
    priority: bool
    features: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.value,
            "name": self.name,
            "price_usd": str(self.price_usd),
            "ai_actions": self.ai_actions,
            "llm_budget_usd": str(self.llm_budget_usd),
            "max_live_agents": self.max_live_agents,
            "max_live_stores": self.max_live_stores,
            "priority": self.priority,
            "features": list(self.features),
        }


PLANS: dict[Plan, PlanSpec] = {
    Plan.FREE: PlanSpec(
        Plan.FREE,
        "Free",
        Decimal(0),
        ai_actions=100,
        llm_budget_usd=Decimal(1),
        max_live_agents=1,
        max_live_stores=1,
        priority=False,
        features=("Full demo store", "1 agent on your live store", "100 AI actions / month"),
    ),
    Plan.GROWTH: PlanSpec(
        Plan.GROWTH,
        "Growth",
        Decimal(49),
        ai_actions=2000,
        llm_budget_usd=Decimal(25),
        max_live_agents=None,
        max_live_stores=1,
        priority=False,
        features=("All 8 agents", "2,000 AI actions / month", "Approvals, audit log, CSV export"),
    ),
    Plan.PRO: PlanSpec(
        Plan.PRO,
        "Pro",
        Decimal(199),
        ai_actions=None,
        llm_budget_usd=Decimal(250),
        max_live_agents=None,
        max_live_stores=None,
        priority=True,
        features=("Everything in Growth", "Unlimited stores and AI actions", "Priority processing"),
    ),
}


def spec(shop: Shop) -> PlanSpec:
    return PLANS[shop.plan]


def period_start(now: datetime) -> date:
    return now.date().replace(day=1)


async def increment(
    db: AsyncSession, shop_id: uuid.UUID, metric: str, amount: int, now: datetime
) -> None:
    await db.execute(
        insert(UsageCounter)
        .values(
            id=uuid.uuid4(), shop_id=shop_id, period=period_start(now), metric=metric, value=amount
        )
        .on_conflict_do_update(
            index_elements=["shop_id", "period", "metric"],
            set_={"value": UsageCounter.value + amount, "updated_at": now},
        )
    )


async def usage(db: AsyncSession, shop_id: uuid.UUID, now: datetime) -> dict[str, int]:
    rows = (
        await db.execute(
            select(UsageCounter.metric, UsageCounter.value).where(
                UsageCounter.shop_id == shop_id, UsageCounter.period == period_start(now)
            )
        )
    ).all()
    values = {metric: int(value) for metric, value in rows}
    return {AI_ACTIONS: values.get(AI_ACTIONS, 0), LLM_COST: values.get(LLM_COST, 0)}


def metered(shop: Shop) -> bool:
    """Demo stores are free and unmetered; limits apply to live stores."""
    return shop.mode == ShopMode.LIVE


async def action_limit_reason(db: AsyncSession, shop: Shop, now: datetime) -> str | None:
    limit = spec(shop).ai_actions
    if not metered(shop) or limit is None:
        return None
    used = (await usage(db, shop.id, now))[AI_ACTIONS]
    if used >= limit:
        return f"Monthly limit of {limit:,} AI actions reached on the {spec(shop).name} plan"
    return None


async def llm_budget_left(db: AsyncSession, shop: Shop, now: datetime) -> bool:
    if not metered(shop):
        return True
    used_micro = (await usage(db, shop.id, now))[LLM_COST]
    return Decimal(used_micro) / Decimal(1_000_000) < spec(shop).llm_budget_usd


async def maybe_alert(db: AsyncSession, shop: Shop, now: datetime) -> None:
    """Notify once per month when AI actions cross 80% and 100% of the plan."""
    limit = spec(shop).ai_actions
    if not metered(shop) or not limit:
        return
    used = (await usage(db, shop.id, now))[AI_ACTIONS]
    period = period_start(now).isoformat()
    for threshold in ALERT_THRESHOLDS:
        if used < limit * threshold:
            continue
        exists = await db.scalar(
            select(func.count(Notification.id)).where(
                Notification.shop_id == shop.id,
                Notification.kind == "billing.usage",
                Notification.data["period"].as_string() == period,
                Notification.data["threshold"].as_string() == str(threshold),
            )
        )
        if exists:
            continue
        full = threshold >= 1.0
        db.add(
            Notification(
                shop_id=shop.id,
                kind="billing.usage",
                severity=Severity.CRITICAL if full else Severity.WARNING,
                title=(
                    f"AI action limit reached ({used:,}/{limit:,})"
                    if full
                    else f"{int(threshold * 100)}% of this month's AI actions used"
                ),
                body=(
                    "Agents keep analysing but won't act until next month or until you upgrade."
                    if full
                    else "Upgrade to keep agents acting without interruption."
                ),
                data={"period": period, "threshold": str(threshold), "used": used, "limit": limit},
            )
        )


async def live_store_limit_reason(
    db: AsyncSession, user_id: uuid.UUID, new_shop: str
) -> str | None:
    """Owners need Pro on one of their live stores to connect more than one."""
    owned = (
        await db.scalars(
            select(Shop)
            .join(Membership, Membership.shop_id == Shop.id)
            .where(
                Membership.user_id == user_id,
                Membership.role == Role.OWNER,
                Shop.mode == ShopMode.LIVE,
                Shop.status == ShopStatus.ACTIVE,
                Shop.domain != new_shop,
            )
        )
    ).all()
    if not owned or any(s.plan == Plan.PRO for s in owned):
        return None
    return "Connecting more than one live store requires the Pro plan."


async def apply_plan(
    db: AsyncSession,
    shop: Shop,
    plan: Plan,
    *,
    provider: BillingProvider,
    external_id: str | None,
    actor_type: ActorType,
    actor_id: str | uuid.UUID | None,
    now: datetime,
) -> None:
    """Switch the shop's plan and keep its subscription record and agent limits consistent."""
    previous = shop.plan
    shop.plan = plan
    subscription = await db.scalar(
        select(Subscription)
        .where(Subscription.shop_id == shop.id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    if subscription is None:
        subscription = Subscription(shop_id=shop.id, plan=plan)
        db.add(subscription)
    subscription.plan = plan
    subscription.provider = provider
    subscription.external_id = external_id
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.cancelled_at = None
    if previous != plan and PLANS[plan].max_live_agents is not None and metered(shop):
        # Downgrade: keep only the allowed number of agents running (Fraud Guard first).
        configs = (
            await db.scalars(
                select(AgentConfig).where(
                    AgentConfig.shop_id == shop.id,
                    AgentConfig.agent != AgentName.ORCHESTRATOR,
                    AgentConfig.enabled.is_(True),
                )
            )
        ).all()
        keep = sorted(configs, key=lambda c: c.agent != AgentName.FRAUD_GUARD)
        for config in keep[PLANS[plan].max_live_agents :]:
            config.enabled = False
    audit.record(
        db,
        shop_id=shop.id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="billing.plan_changed",
        target_type="shop",
        target_id=shop.id,
        details={"from": previous.value, "to": plan.value, "provider": provider.value},
    )
    await db.flush()
