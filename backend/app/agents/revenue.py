"""Revenue Analyst: compares the live sales pulse with the same window last week and explains
anomalies with the most likely cause."""

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity import money
from app.agents.base import AgentContext, Decision
from app.models import Notification, Order, OrderItem, Product, Shop, Variant
from app.models.enums import AgentName, Severity
from app.pipeline.normalize import Change
from app.services.metrics import day_start

ANOMALY_THRESHOLD = 0.4
MIN_BASELINE_MINOR = 20_000
MIN_BASELINE_ORDERS = 3
EVALUATE_EVERY_SECONDS = 300
ALERT_EVERY_SECONDS = 3600


async def _orders(db: AsyncSession, shop: Shop, start: datetime, end: datetime) -> list[Order]:
    return list(
        (
            await db.scalars(
                select(Order).where(
                    Order.shop_id == shop.id,
                    Order.processed_at >= start,
                    Order.processed_at < end,
                )
            )
        ).all()
    )


def _revenue(orders: list[Order]) -> int:
    return sum(o.total_minor - o.refunded_minor for o in orders if o.cancelled_at is None)


async def likely_cause(
    db: AsyncSession,
    shop: Shop,
    now: datetime,
    current: list[Order],
    baseline: list[Order],
    direction: str,
) -> str:
    # 1. A best-seller out of stock.
    if direction == "down":
        top = (
            await db.execute(
                select(
                    Product.title,
                    Variant.title,
                    func.sum(OrderItem.price_minor * OrderItem.quantity),
                )
                .join(Variant, Variant.id == OrderItem.variant_id)
                .join(Product, Product.id == Variant.product_id)
                .join(Order, Order.id == OrderItem.order_id)
                .where(
                    OrderItem.shop_id == shop.id,
                    Order.processed_at >= now - timedelta(days=14),
                    Variant.inventory_quantity <= 0,
                )
                .group_by(Product.title, Variant.title)
                .order_by(func.sum(OrderItem.price_minor * OrderItem.quantity).desc())
                .limit(1)
            )
        ).first()
        if top is not None:
            return f"stockout on {top[0]} / {top[1]}, one of your best sellers"
    # 2. Refund wave.
    refunds_now = sum(o.refunded_minor for o in current)
    refunds_before = sum(o.refunded_minor for o in baseline)
    if direction == "down" and refunds_now > max(3 * refunds_before, 10_000):
        return f"a refund wave ({money(refunds_now, shop.currency)} refunded so far)"

    # 3. Promotions drive spikes.
    discount_now = sum(o.discount_minor for o in current)
    discount_before = sum(o.discount_minor for o in baseline)
    if direction == "up" and discount_now > 2 * max(discount_before, 1) and discount_now > 5_000:
        return "heavy discount usage (a promotion or code in circulation)"

    # 4. Traffic source mix, in the same direction as the move.
    def shares(orders: list[Order]) -> dict[str, float]:
        counts = Counter(o.source_name or "unknown" for o in orders)
        total = sum(counts.values()) or 1
        return {k: v / total for k, v in counts.items()}

    now_mix, before_mix = shares(current), shares(baseline)
    sign = 1 if direction == "up" else -1
    deltas = {s: now_mix.get(s, 0) - before_mix.get(s, 0) for s in set(now_mix) | set(before_mix)}
    if current and baseline and deltas:
        source = max(deltas, key=lambda s: sign * deltas[s])
        if sign * deltas[source] >= 0.2:
            verb = "surge in" if direction == "up" else "drop in"
            share_now, share_before = now_mix.get(source, 0), before_mix.get(source, 0)
            return f"a {verb} {source} orders ({share_now:.0%} of orders vs {share_before:.0%})"
    return "no single cause stands out; keep an eye on it"


class RevenueAnalyst:
    name: ClassVar[AgentName] = AgentName.REVENUE_ANALYST
    triggers: ClassVar[frozenset[str]] = frozenset({"order.created", "order.refunded"})

    async def decide(self, ctx: AgentContext, change: Change) -> Decision | None:
        # Evaluate at most every few minutes per shop; orders arrive far faster than that.
        throttle = f"shop:{ctx.shop.id}:revenue_analyst:eval"
        if not await ctx.redis.set(throttle, "1", nx=True, ex=EVALUATE_EVERY_SECONDS):
            return None
        now = ctx.now
        start = day_start(ctx.shop, now)
        current = await _orders(ctx.db, ctx.shop, start, now)
        baseline = await _orders(
            ctx.db, ctx.shop, start - timedelta(days=7), now - timedelta(days=7)
        )
        now_rev, base_rev = _revenue(current), _revenue(baseline)
        output: dict[str, Any] = {
            "revenue_minor": now_rev,
            "baseline_minor": base_rev,
            "orders": len(current),
            "baseline_orders": len(baseline),
        }
        decision = Decision(output=output)
        if base_rev < MIN_BASELINE_MINOR or len(baseline) < MIN_BASELINE_ORDERS:
            output["skipped"] = "not enough history for this time of day"
            return decision
        change_ratio = (now_rev - base_rev) / base_rev
        output["change"] = round(change_ratio, 3)
        if abs(change_ratio) < ANOMALY_THRESHOLD:
            return decision
        direction = "up" if change_ratio > 0 else "down"
        alert = f"shop:{ctx.shop.id}:revenue_analyst:alert:{direction}"
        if not await ctx.redis.set(alert, "1", nx=True, ex=ALERT_EVERY_SECONDS):
            output["skipped"] = "already flagged this hour"
            return decision
        cause = await likely_cause(ctx.db, ctx.shop, now, current, baseline, direction)
        currency = ctx.shop.currency
        headline = (
            f"Revenue is {direction} {abs(change_ratio):.0%} vs the same time last week "
            f"({money(now_rev, currency)} vs {money(base_rev, currency)})"
        )
        output.update({"anomaly": direction, "headline": headline, "cause": cause})
        return decision

    async def after_decide(self, ctx: AgentContext, change: Change, decision: Decision) -> None:
        out = decision.output
        if "anomaly" not in out:
            return
        ctx.db.add(
            Notification(
                shop_id=ctx.shop.id,
                kind="insight.anomaly",
                severity=Severity.WARNING if out["anomaly"] == "down" else Severity.INFO,
                title=out["headline"],
                body=f"Likely cause: {out['cause']}.",
                data={
                    k: out[k]
                    for k in ("anomaly", "change", "revenue_minor", "baseline_minor", "cause")
                },
            )
        )
        await ctx.db.flush()
