import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentConfig, Membership, Shop, ShopSettings, Subscription, User
from app.models.enums import (
    ActorType,
    AgentName,
    Autonomy,
    Plan,
    Role,
    ShopMode,
    SubscriptionStatus,
)
from app.services import audit

# Safe defaults: anything with external side effects starts in SUGGEST (approval required).
DEFAULT_AUTONOMY: dict[AgentName, Autonomy] = {
    AgentName.ORCHESTRATOR: Autonomy.AUTO,
    AgentName.FRAUD_GUARD: Autonomy.SUGGEST,
    AgentName.INVENTORY_PLANNER: Autonomy.SUGGEST,
    AgentName.CART_RECOVERY: Autonomy.SUGGEST,
    AgentName.SUPPORT: Autonomy.SUGGEST,
    AgentName.REVIEW_REPUTATION: Autonomy.SUGGEST,
    AgentName.REVENUE_ANALYST: Autonomy.AUTO,
    AgentName.PRICING_ADVISOR: Autonomy.SUGGEST,
}

DEFAULT_AGENT_SETTINGS: dict[AgentName, dict[str, object]] = {
    AgentName.CART_RECOVERY: {
        "abandon_after_minutes": 60,
        "reminder_interval_minutes": 1440,
        "discount_pct": 10,
    },
    AgentName.INVENTORY_PLANNER: {"supplier_email": None},
}


async def create_shop(
    db: AsyncSession,
    *,
    owner: User,
    name: str,
    domain: str,
    mode: ShopMode,
    currency: str = "USD",
    timezone: str = "UTC",
) -> Shop:
    shop = Shop(
        id=uuid.uuid4(),
        name=name,
        domain=domain,
        mode=mode,
        currency=currency,
        timezone=timezone,
        plan=Plan.FREE,
    )
    db.add(shop)
    await db.flush()
    db.add(ShopSettings(shop_id=shop.id))
    db.add(Membership(shop_id=shop.id, user_id=owner.id, role=Role.OWNER))
    db.add(
        Subscription(
            shop_id=shop.id,
            plan=Plan.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
    )
    for agent, autonomy in DEFAULT_AUTONOMY.items():
        db.add(
            AgentConfig(
                shop_id=shop.id,
                agent=agent,
                autonomy=autonomy,
                enabled=True,
                settings=dict(DEFAULT_AGENT_SETTINGS.get(agent, {})),
            )
        )
    audit.record(
        db,
        shop_id=shop.id,
        actor_type=ActorType.USER,
        actor_id=owner.id,
        action="shop.created",
        target_type="shop",
        target_id=shop.id,
        details={"mode": mode.value, "domain": domain},
    )
    await db.flush()
    return shop
