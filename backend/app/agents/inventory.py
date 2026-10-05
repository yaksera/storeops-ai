"""Inventory Planner: re-forecasts on every stock change and drafts supplier POs."""

import uuid
from datetime import date, datetime, timedelta
from typing import ClassVar
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext, Decision, ProposalDraft
from app.agents.forecast import Forecast, forecast
from app.models import ActionProposal, Notification, Order, OrderItem, Product, Shop, Variant
from app.models.enums import AgentName, ProposalStatus, RiskLevel, Severity
from app.pipeline.normalize import Change

DEFAULT_SUPPLIER_NOTE = "Set a supplier email in the Inventory Planner settings."


async def sales_history(
    db: AsyncSession, shop: Shop, variant_ids: list[uuid.UUID], now: datetime, days: int = 90
) -> dict[uuid.UUID, dict[date, int]]:
    """Units sold per variant per local day."""
    local_day = func.date(func.timezone(shop.timezone, Order.processed_at))
    rows = (
        await db.execute(
            select(OrderItem.variant_id, local_day, func.sum(OrderItem.quantity))
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                OrderItem.shop_id == shop.id,
                OrderItem.variant_id.in_(variant_ids),
                Order.processed_at >= now - timedelta(days=days + 1),
                Order.cancelled_at.is_(None),
            )
            .group_by(OrderItem.variant_id, local_day)
        )
    ).all()
    history: dict[uuid.UUID, dict[date, int]] = {vid: {} for vid in variant_ids}
    for variant_id, day, units in rows:
        if variant_id is not None:
            history[variant_id][day] = int(units)
    return history


def local_today(shop: Shop, now: datetime) -> date:
    return now.astimezone(ZoneInfo(shop.timezone)).date()


def variant_forecast(variant: Variant, history: dict[date, int], today: date) -> Forecast:
    return forecast(
        on_hand=variant.inventory_quantity,
        history=history,
        today=today,
        lead_time_days=variant.lead_time_days,
        reorder_point=variant.reorder_point,
    )


def needs_reorder(variant: Variant, fc: Forecast) -> bool:
    if fc.suggested_order_qty <= 0:
        return False
    below_rop = variant.inventory_quantity <= fc.reorder_point
    runs_out_soon = (
        fc.days_to_stockout is not None and fc.days_to_stockout <= variant.lead_time_days + 3
    )
    return below_rop or runs_out_soon


class InventoryPlanner:
    name: ClassVar[AgentName] = AgentName.INVENTORY_PLANNER
    triggers: ClassVar[frozenset[str]] = frozenset({"inventory.changed"})

    async def decide(self, ctx: AgentContext, change: Change) -> Decision | None:
        row = (
            await ctx.db.execute(
                select(Variant, Product)
                .join(Product, Product.id == Variant.product_id)
                .where(Variant.id == uuid.UUID(change.data["variant_id"]))
            )
        ).one_or_none()
        if row is None:
            return None
        variant, product = row
        history = (await sales_history(ctx.db, ctx.shop, [variant.id], ctx.now))[variant.id]
        fc = variant_forecast(variant, history, local_today(ctx.shop, ctx.now))
        label = f"{product.title} / {variant.title}"
        decision = Decision(
            output={
                "variant_id": str(variant.id),
                "sku": variant.sku,
                "on_hand": variant.inventory_quantity,
                "daily_rate_30d": fc.daily_rate_30d,
                "daily_rate_90d": fc.daily_rate_90d,
                "days_to_stockout": fc.days_to_stockout,
                "reorder_point": fc.reorder_point,
                "suggested_order_qty": fc.suggested_order_qty,
                "lead_time_days": variant.lead_time_days,
            }
        )
        if not needs_reorder(variant, fc):
            return decision

        open_po = await ctx.db.scalar(
            select(func.count(ActionProposal.id)).where(
                ActionProposal.shop_id == ctx.shop.id,
                ActionProposal.action_type == "draft_po",
                ActionProposal.target_id == str(variant.id),
                ActionProposal.status.in_([ProposalStatus.PROPOSED, ProposalStatus.APPROVED]),
            )
        )
        if open_po:
            decision.output["skipped"] = "purchase order already pending"
            return decision

        supplier = ctx.config.settings.get("supplier_email")
        stockout = (
            "is out of stock"
            if variant.inventory_quantity <= 0
            else f"runs out in about {fc.days_to_stockout:g} days"
            if fc.days_to_stockout is not None
            else f"is below its reorder point of {fc.reorder_point}"
        )
        qty = fc.suggested_order_qty
        sender = ctx.settings.sender_name or ctx.shop.name
        subject = f"Purchase order: {qty} x {variant.sku or label}"
        body = (
            f"Hello,\n\n{sender} would like to order:\n\n"
            f"  {qty} x {label} (SKU {variant.sku})\n\n"
            f"Please confirm availability and ship date. Our usual lead time is "
            f"{variant.lead_time_days} days.\n\nThanks,\n{sender}"
        )
        decision.proposals.append(
            ProposalDraft(
                action_type="draft_po",
                title=f"Reorder {qty} x {label}",
                summary=f"{label} {stockout} at ~{fc.daily_rate_30d:g} units/day "
                f"({variant.inventory_quantity} on hand, lead time {variant.lead_time_days} days).",
                rationale=f"Covers {variant.lead_time_days} days of lead time plus 30 days of "
                f"demand from the 30/90-day moving average with weekday seasonality.",
                risk_level=RiskLevel.MEDIUM,
                payload={
                    "variant_id": str(variant.id),
                    "sku": variant.sku,
                    "quantity": qty,
                    "to": supplier,
                    "subject": subject,
                    "body": body,
                },
                preview={
                    "email": {
                        "to": supplier or DEFAULT_SUPPLIER_NOTE,
                        "subject": subject,
                        "body": body,
                    },
                    "forecast": decision.output,
                },
                target_type="variant",
                target_id=str(variant.id),
                always_requires_approval=True,
                expires_in=timedelta(days=3),
            )
        )
        ctx.db.add(
            Notification(
                shop_id=ctx.shop.id,
                kind="inventory.low",
                severity=Severity.WARNING,
                title=f"{label} {stockout}",
                body=f"Purchase order for {qty} units drafted for your approval.",
                data={"variant_id": str(variant.id)},
            )
        )
        return decision

    async def after_decide(self, ctx: AgentContext, change: Change, decision: Decision) -> None:
        return None
