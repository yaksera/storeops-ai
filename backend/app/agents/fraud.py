"""Fraud Guard: scores every new order 0-100 and proposes a hold at or above the threshold."""

import uuid
from datetime import timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents.base import AgentContext, Decision, ProposalDraft
from app.models import Customer, Notification, Order, OrderItem
from app.models.enums import AgentName, RiskLevel, Severity
from app.pipeline.normalize import Change, order_summary
from app.services import events

PROMPT_VERSION = "fraud-v1"
HIGH_RISK_COUNTRIES = frozenset({"NG", "RU", "VN", "ID", "BR", "UA", "PK"})
DISPOSABLE_DOMAINS = frozenset(
    {"quickmail.example", "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com"}
)
HIGH_VALUE_MINOR = 50_000


class Explanation(BaseModel):
    explanation: str = Field(max_length=400)


def score_order(
    order: Order, items: list[OrderItem], customer: Customer | None, recent_orders: int
) -> tuple[int, list[dict[str, Any]]]:
    """Deterministic risk score. Each factor is (points, plain-language reason)."""
    factors: list[dict[str, Any]] = []

    def add(points: int, reason: str, code: str) -> None:
        factors.append({"points": points, "reason": reason, "code": code})

    billing, shipping = order.billing_country, order.shipping_country
    if billing and shipping and billing != shipping:
        add(25, f"billed in {billing} but shipping to {shipping}", "address_mismatch")
    risky = sorted({c for c in (billing, shipping) if c in HIGH_RISK_COUNTRIES})
    if risky:
        add(25, f"high-risk country ({', '.join(risky)})", "high_risk_country")
    if order.financial_status in {"pending", "voided"} or order.financial_status is None:
        add(15, "payment not captured", "payment_pending")
    max_qty = max((i.quantity for i in items), default=0)
    units = sum(i.quantity for i in items)
    if max_qty >= 3 or units >= 6:
        top = max(items, key=lambda i: i.quantity)
        add(15, f"unusual quantity ({top.quantity}x {top.title})", "unusual_quantity")
    is_new = customer is None or customer.orders_count <= 1
    if is_new and order.total_minor >= HIGH_VALUE_MINOR:
        add(
            20,
            f"new customer spending {order.total_minor / 100:,.2f} {order.currency}",
            "new_high_value",
        )
    domain = (order.email or "").rsplit("@", 1)[-1].lower()
    if domain in DISPOSABLE_DOMAINS:
        add(10, f"disposable email domain ({domain})", "disposable_email")
    if recent_orders >= 2:
        add(15, f"{recent_orders + 1} orders from this customer within an hour", "velocity")
    return min(100, sum(f["points"] for f in factors)), factors


def explain(score: int, factors: list[dict[str, Any]]) -> str:
    if not factors:
        return f"Scored {score}/100: nothing unusual about this order."
    reasons = [f["reason"] for f in sorted(factors, key=lambda f: -int(f["points"]))]
    joined = reasons[0] if len(reasons) == 1 else ", ".join(reasons[:-1]) + " and " + reasons[-1]
    return f"Scored {score}/100: {joined}."


class FraudGuard:
    name: ClassVar[AgentName] = AgentName.FRAUD_GUARD
    triggers: ClassVar[frozenset[str]] = frozenset({"order.created"})

    async def decide(self, ctx: AgentContext, change: Change) -> Decision | None:
        order = await ctx.db.get(Order, uuid.UUID(change.data["id"]))
        if order is None:
            return None
        items = list(
            (await ctx.db.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all()
        )
        customer = await ctx.db.get(Customer, order.customer_id) if order.customer_id else None
        recent = 0
        if order.customer_id or order.email:
            recent = int(
                await ctx.db.scalar(
                    select(func.count(Order.id)).where(
                        Order.shop_id == ctx.shop.id,
                        Order.id != order.id,
                        Order.processed_at >= order.processed_at - timedelta(hours=1),
                        (Order.customer_id == order.customer_id)
                        if order.customer_id
                        else (Order.email == order.email),
                    )
                )
                or 0
            )
        score, factors = score_order(order, items, customer, recent)
        threshold = ctx.settings.fraud_threshold
        explanation = explain(score, factors)
        decision = Decision(
            output={
                "order_id": str(order.id),
                "order_name": order.name,
                "score": score,
                "threshold": threshold,
                "factors": factors,
                "explanation": explanation,
            }
        )

        if ctx.llm is not None and score >= threshold - 20:
            result = await ctx.llm.complete_json(
                agent=self.name,
                system=(
                    "You are a fraud analyst for an online store. Explain a risk score to a busy "
                    "merchant in one or two plain sentences. No jargon, no speculation beyond "
                    "the factors given."
                ),
                user=f"Order {order.name}, score {score}/100. Factors: "
                + "; ".join(f["reason"] for f in factors),
                schema=Explanation,
                prompt_version=PROMPT_VERSION,
                model=ctx.config.model,
                agent_run_id=ctx.run_id,
            )
            if result is None:
                decision.used_fallback = True
            else:
                explanation = result.value.explanation
                decision.output["explanation"] = explanation
                decision.model = result.model
                decision.prompt_version = PROMPT_VERSION
                decision.cost_usd = float(result.cost_usd)

        if score >= threshold and not order.is_held:
            summary = order_summary(order, customer, sum(i.quantity for i in items))
            decision.proposals.append(
                ProposalDraft(
                    action_type="hold_order",
                    title=f"Hold order {order.name} for review",
                    summary=explanation,
                    rationale=f"Risk score {score} is at or above your hold threshold of "
                    f"{threshold}. Holding stops fulfilment until you release it.",
                    risk_level=RiskLevel.MEDIUM,
                    payload={"order_id": str(order.id), "order_name": order.name, "score": score},
                    preview={"order": summary, "factors": factors},
                    target_type="order",
                    target_id=str(order.id),
                    expires_in=timedelta(hours=12),
                )
            )
        return decision

    async def after_decide(self, ctx: AgentContext, change: Change, decision: Decision) -> None:
        order = await ctx.db.get(Order, uuid.UUID(decision.output["order_id"]))
        if order is None:
            return
        order.risk_score = int(decision.output["score"])
        if order.risk_score >= decision.output["threshold"]:
            ctx.db.add(
                Notification(
                    shop_id=ctx.shop.id,
                    kind="fraud.alert",
                    severity=Severity.CRITICAL,
                    title=f"High-risk order {order.name} ({order.risk_score}/100)",
                    body=decision.output["explanation"],
                    data={"order_id": str(order.id)},
                )
            )
        await ctx.db.flush()
        customer = await ctx.db.get(Customer, order.customer_id) if order.customer_id else None
        data = order_summary(order, customer, int(change.data.get("item_count") or 0))
        await events.publish(ctx.redis, ctx.shop.id, "order.updated", data)
