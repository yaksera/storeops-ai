"""Pricing Advisor (suggest-only): price moves from stock level, sell-through and margin floor."""

import uuid
from datetime import timedelta
from typing import ClassVar

from sqlalchemy import func, select

from app.agents.activity import money
from app.agents.base import AgentContext, Decision, ProposalDraft
from app.agents.inventory import local_today, sales_history, variant_forecast
from app.models import ActionProposal, Product, Variant
from app.models.enums import AgentName, ProposalStatus, RiskLevel
from app.pipeline.normalize import Change

DEFAULT_MARGIN_FLOOR_PCT = 25
MAX_STEP_PCT = 10
OVERSTOCK_DAYS = 120


def round_price(minor: int) -> int:
    """Retail-style price ending in .00 (e.g. 18900 rather than 18733)."""
    return max(100, round(minor / 100.0) * 100)


class PricingAdvisor:
    name: ClassVar[AgentName] = AgentName.PRICING_ADVISOR
    triggers: ClassVar[frozenset[str]] = frozenset({"inventory.changed"})

    async def decide(self, ctx: AgentContext, change: Change) -> Decision | None:
        variant_id = uuid.UUID(change.data["variant_id"])
        # At most one look per variant per day.
        if not await ctx.redis.set(
            f"shop:{ctx.shop.id}:pricing:{variant_id}", "1", nx=True, ex=86_400
        ):
            return None
        row = (
            await ctx.db.execute(
                select(Variant, Product)
                .join(Product, Product.id == Variant.product_id)
                .where(Variant.id == variant_id)
            )
        ).one_or_none()
        if row is None:
            return None
        variant, product = row
        history = (await sales_history(ctx.db, ctx.shop, [variant.id], ctx.now, days=30))[
            variant.id
        ]
        fc = variant_forecast(variant, history, local_today(ctx.shop, ctx.now))
        sold_14 = sum(
            units
            for day, units in history.items()
            if (local_today(ctx.shop, ctx.now) - day).days <= 14
        )
        sell_through = sold_14 / (sold_14 + max(variant.inventory_quantity, 0)) if sold_14 else 0.0
        floor_pct = int(ctx.config.settings.get("margin_floor_pct", DEFAULT_MARGIN_FLOOR_PCT))
        floor = (
            round_price(int((variant.cost_minor or 0) * (1 + floor_pct / 100)))
            if variant.cost_minor
            else 0
        )
        decision = Decision(
            output={
                "variant_id": str(variant.id),
                "price_minor": variant.price_minor,
                "sell_through_14d": round(sell_through, 3),
                "days_to_stockout": fc.days_to_stockout,
                "margin_floor_minor": floor,
            }
        )
        label = f"{product.title} / {variant.title}"
        new_price: int | None = None
        reason = ""
        if (
            fc.days_to_stockout is not None
            and 0 < fc.days_to_stockout <= variant.lead_time_days
            and sell_through >= 0.3
        ):
            step = 5 if fc.days_to_stockout > variant.lead_time_days / 2 else MAX_STEP_PCT
            new_price = round_price(variant.price_minor * (100 + step) // 100)
            reason = (
                f"Demand is outpacing supply: {sell_through:.0%} sell-through in 14 days and "
                f"stock runs out in ~{fc.days_to_stockout:g} days, before a reorder can land "
                f"({variant.lead_time_days}-day lead time). A {step}% increase slows the run-down."
            )
        elif (fc.days_to_stockout is None or fc.days_to_stockout > OVERSTOCK_DAYS) and (
            variant.inventory_quantity > 0 and sell_through < 0.1
        ):
            candidate = round_price(variant.price_minor * (100 - MAX_STEP_PCT) // 100)
            if candidate >= floor:
                new_price = candidate
                reason = (
                    f"Slow mover: {sell_through:.0%} sell-through in 14 days with "
                    f"{variant.inventory_quantity} on hand. A {MAX_STEP_PCT}% markdown stays above "
                    f"your {floor_pct}% margin floor ({money(floor, ctx.shop.currency)})."
                )
        if new_price is None or new_price == variant.price_minor:
            return decision

        pending = await ctx.db.scalar(
            select(func.count(ActionProposal.id)).where(
                ActionProposal.shop_id == ctx.shop.id,
                ActionProposal.action_type == "change_price",
                ActionProposal.target_id == str(variant.id),
                ActionProposal.status == ProposalStatus.PROPOSED,
            )
        )
        if pending:
            return decision
        decision.output["suggested_price_minor"] = new_price
        currency = ctx.shop.currency
        decision.proposals.append(
            ProposalDraft(
                action_type="change_price",
                title=f"{'Raise' if new_price > variant.price_minor else 'Lower'} {label} to "
                f"{money(new_price, currency)}",
                summary=f"{money(variant.price_minor, currency)} → {money(new_price, currency)}",
                rationale=reason,
                risk_level=RiskLevel.HIGH,
                payload={
                    "variant_id": str(variant.id),
                    "from_minor": variant.price_minor,
                    "to_minor": new_price,
                    "floor_minor": floor,
                },
                preview={
                    "price": {
                        "from_minor": variant.price_minor,
                        "to_minor": new_price,
                        "currency": currency,
                    }
                },
                target_type="variant",
                target_id=str(variant.id),
                always_requires_approval=True,
                expires_in=timedelta(days=2),
            )
        )
        return decision

    async def after_decide(self, ctx: AgentContext, change: Change, decision: Decision) -> None:
        return None
