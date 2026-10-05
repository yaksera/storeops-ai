from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Checkout, Order, Shop
from app.models.base import utcnow
from app.models.enums import CheckoutStatus


@dataclass(frozen=True, slots=True)
class Kpis:
    currency: str
    revenue_minor: int
    orders: int
    aov_minor: int
    conversion_rate: float
    checkouts: int
    recovered_revenue_minor: int
    revenue_last_week_minor: int
    orders_last_week: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def day_start(shop: Shop, now: datetime) -> datetime:
    tz = ZoneInfo(shop.timezone)
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def _revenue(db: AsyncSession, shop: Shop, start: datetime, end: datetime) -> tuple[int, int]:
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(Order.total_minor - Order.refunded_minor), 0),
                func.count(Order.id),
            ).where(
                Order.shop_id == shop.id,
                Order.processed_at >= start,
                Order.processed_at < end,
                Order.cancelled_at.is_(None),
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def compute_kpis(db: AsyncSession, shop: Shop, now: datetime | None = None) -> Kpis:
    """Today's numbers in the shop's timezone, plus the same window a week earlier."""
    now = now or utcnow()
    start = day_start(shop, now)
    revenue, orders = await _revenue(db, shop, start, now)
    revenue_lw, orders_lw = await _revenue(
        db, shop, start - timedelta(days=7), now - timedelta(days=7)
    )
    checkouts = int(
        await db.scalar(
            select(func.count(Checkout.id)).where(
                Checkout.shop_id == shop.id,
                Checkout.shopify_created_at >= start,
                Checkout.shopify_created_at < now,
            )
        )
        or 0
    )
    recovered = int(
        await db.scalar(
            select(func.coalesce(func.sum(Order.total_minor), 0))
            .join(Checkout, Checkout.recovered_order_id == Order.id)
            .where(
                Checkout.shop_id == shop.id,
                Checkout.status == CheckoutStatus.RECOVERED,
                Order.processed_at >= start,
            )
        )
        or 0
    )
    return Kpis(
        currency=shop.currency,
        revenue_minor=revenue,
        orders=orders,
        aov_minor=revenue // orders if orders else 0,
        conversion_rate=round(min(1.0, orders / checkouts), 4) if checkouts else 0.0,
        checkouts=checkouts,
        recovered_revenue_minor=recovered,
        revenue_last_week_minor=revenue_lw,
        orders_last_week=orders_lw,
    )


async def revenue_by_hour(
    db: AsyncSession, shop: Shop, now: datetime | None = None
) -> dict[str, list[int]]:
    """Revenue per local hour for today and for the same weekday last week."""
    now = now or utcnow()
    start = day_start(shop, now)
    result: dict[str, list[int]] = {}
    for label, offset in (("today", 0), ("last_week", 7)):
        window_start = start - timedelta(days=offset)
        hour = func.extract("hour", func.timezone(shop.timezone, Order.processed_at))
        rows = (
            await db.execute(
                select(hour, func.sum(Order.total_minor - Order.refunded_minor))
                .where(
                    Order.shop_id == shop.id,
                    Order.processed_at >= window_start,
                    Order.processed_at < window_start + timedelta(days=1),
                    Order.cancelled_at.is_(None),
                )
                .group_by(hour)
            )
        ).all()
        buckets = [0] * 24
        for h, total in rows:
            buckets[int(h)] = int(total or 0)
        result[label] = buckets
    return result
