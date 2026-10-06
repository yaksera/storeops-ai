"""Periodic agent work that isn't triggered by a webhook."""

from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy import String, and_, cast, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import orchestrator
from app.models import ActionProposal, AgentConfig, Checkout, Customer, Shop
from app.models.base import utcnow
from app.models.enums import AgentName, Autonomy, CheckoutStatus, ProposalStatus, ShopStatus
from app.pipeline.normalize import Change

SCAN_BATCH = 20


async def scan_abandoned_checkouts(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Redis
) -> int:
    """Hand checkouts that crossed the abandonment threshold to Cart Recovery."""
    now = utcnow()
    routed = 0
    async with sessionmaker() as db:
        configs = (
            await db.execute(
                select(AgentConfig, Shop)
                .join(Shop, Shop.id == AgentConfig.shop_id)
                .where(
                    AgentConfig.agent == AgentName.CART_RECOVERY,
                    AgentConfig.enabled.is_(True),
                    AgentConfig.autonomy != Autonomy.OFF,
                    Shop.status == ShopStatus.ACTIVE,
                )
            )
        ).all()
        for config, shop in configs:
            abandon_after = timedelta(minutes=int(config.settings.get("abandon_after_minutes", 60)))
            interval = timedelta(
                minutes=int(config.settings.get("reminder_interval_minutes", 1440))
            )
            pending = exists().where(
                ActionProposal.shop_id == shop.id,
                ActionProposal.target_type == "checkout",
                ActionProposal.target_id == cast(Checkout.id, String),
                ActionProposal.status.in_([ProposalStatus.PROPOSED, ProposalStatus.APPROVED]),
            )
            rows = (
                await db.execute(
                    select(Checkout, Customer)
                    .outerjoin(Customer, Customer.id == Checkout.customer_id)
                    .where(
                        Checkout.shop_id == shop.id,
                        Checkout.email.is_not(None),
                        Checkout.shopify_created_at > now - timedelta(days=7),
                        Checkout.shopify_created_at < now - abandon_after,
                        or_(
                            and_(
                                Checkout.status == CheckoutStatus.OPEN,
                                Checkout.reminders_sent == 0,
                            ),
                            and_(
                                Checkout.status == CheckoutStatus.ABANDONED,
                                Checkout.reminders_sent == 1,
                                Checkout.last_reminder_at < now - interval,
                            ),
                        ),
                        ~pending,
                    )
                    .order_by(Checkout.shopify_created_at)
                    .limit(SCAN_BATCH)
                )
            ).all()
            for checkout, customer in rows:
                checkout.status = CheckoutStatus.ABANDONED
                await db.commit()
                change = Change(
                    "checkout.abandoned",
                    {
                        "id": str(checkout.id),
                        "token": checkout.token,
                        "status": checkout.status.value,
                        "total_minor": checkout.total_minor,
                        "currency": checkout.currency,
                        "item_count": sum(
                            int(li.get("quantity") or 1) for li in checkout.line_items
                        ),
                        "customer_name": customer.first_name if customer else None,
                        "reminders_sent": checkout.reminders_sent,
                    },
                )
                await orchestrator.route(db, redis, shop, [change])
                routed += 1
    return routed
