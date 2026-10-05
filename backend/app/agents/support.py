"""Support Agent: answers order-status, returns and sizing questions with live order data, and
hands anything risky to a human."""

import re
import uuid
from datetime import timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.base import AgentContext, Decision, ProposalDraft
from app.models import Message, Notification, Order, Ticket
from app.models.enums import (
    AgentName,
    MessageDirection,
    RiskLevel,
    Severity,
    TicketStatus,
)
from app.pipeline.normalize import Change
from app.services import events

PROMPT_VERSION = "support-v1"
REFUND_ESCALATION_MINOR = 10_000

INTENTS: dict[str, tuple[str, ...]] = {
    "order_status": (
        "where is", "where's", "tracking", "track", "shipped", "delivery", "deliver",
        "arrive", "hasn't moved", "status", "still waiting", "when will",
    ),
    "returns": ("return", "exchange", "send back", "refund", "swap"),
    "sizing": ("size", "sizing", "fit", "runs small", "runs large", "true to size"),
    "order_change": ("change my order", "change the colour", "change the color", "cancel"),
}  # fmt: skip
ANGRY = (
    "unacceptable", "terrible", "furious", "worst", "scam", "never buying", "ridiculous",
    "disgusted", "useless", "fed up",
)  # fmt: skip
LEGAL = (
    "lawyer", "attorney", "legal action", "chargeback", "charge back", "disput", "sue ", "court",
    "small claims", "consumer protection", "bbb",
)  # fmt: skip
ORDER_REF = re.compile(r"#\d{3,}")

SIZING_GUIDE = (
    "Our apparel runs true to size. If you're between sizes or plan to layer underneath, size "
    "up. Footwear fits true to size with room for a hiking sock."
)


class Reply(BaseModel):
    body: str = Field(max_length=2000)


def classify(text: str) -> tuple[str | None, float]:
    """Best-matching intent and a rough confidence in [0, 1]."""
    lowered = text.lower()
    scores = {intent: sum(word in lowered for word in words) for intent, words in INTENTS.items()}
    best = max(scores, key=lambda k: scores[k])
    hits = scores[best]
    if hits == 0:
        return None, 0.0
    runner_up = sorted(scores.values())[-2]
    confidence = min(1.0, 0.5 + 0.2 * hits - 0.15 * runner_up)
    return best, round(confidence, 2)


def escalation_reasons(
    text: str, intent: str | None, confidence: float, order: Order | None
) -> list[str]:
    lowered = text.lower()
    reasons: list[str] = []
    letters = [c for c in text if c.isalpha()]
    shouting = len(letters) > 20 and sum(c.isupper() for c in letters) / len(letters) > 0.6
    if shouting or any(word in lowered for word in ANGRY) or "!!" in text:
        reasons.append("customer is upset")
    if any(word in lowered for word in LEGAL):
        reasons.append("legal or chargeback language")
    if (
        intent == "returns"
        and "refund" in lowered
        and order
        and order.total_minor > REFUND_ESCALATION_MINOR
    ):
        reasons.append(f"refund request above {REFUND_ESCALATION_MINOR // 100} {order.currency}")
    if intent is None or confidence < 0.6:
        reasons.append("not confident about what they need")
    if intent == "order_change":
        reasons.append("order changes need a human")
    return reasons


def _status_line(order: Order) -> str:
    if order.cancelled_at:
        return f"Order {order.name} was cancelled."
    if order.is_held:
        return (
            f"Order {order.name} is going through a quick security review before it ships. "
            "We'll email you as soon as it's on its way."
        )
    if order.fulfillment_status == "fulfilled":
        tracking = (
            f" Tracking number {order.tracking_number}"
            + (f": {order.tracking_url}" if order.tracking_url else "")
            + "."
            if order.tracking_number
            else ""
        )
        return f"Good news: order {order.name} has shipped.{tracking}"
    return (
        f"Order {order.name} is confirmed and being packed. Orders usually ship within "
        "1-2 business days, and you'll get tracking by email as soon as it leaves us."
    )


class SupportAgent:
    name: ClassVar[AgentName] = AgentName.SUPPORT
    triggers: ClassVar[frozenset[str]] = frozenset({"ticket.created"})

    async def _order_for(self, ctx: AgentContext, ticket: Ticket, text: str) -> Order | None:
        if ticket.order_id:
            return await ctx.db.get(Order, ticket.order_id)
        match = ORDER_REF.search(text)
        if match:
            order = await ctx.db.scalar(
                select(Order).where(Order.shop_id == ctx.shop.id, Order.name == match.group(0))
            )
            if order is not None:
                return order
        if ticket.customer_email:
            return await ctx.db.scalar(
                select(Order)
                .where(Order.shop_id == ctx.shop.id, Order.email == ticket.customer_email)
                .order_by(Order.processed_at.desc())
                .limit(1)
            )
        return None

    async def decide(self, ctx: AgentContext, change: Change) -> Decision | None:
        ticket = await ctx.db.get(Ticket, uuid.UUID(change.data["id"]))
        if ticket is None:
            return None
        message = await ctx.db.scalar(
            select(Message)
            .where(Message.ticket_id == ticket.id, Message.direction == MessageDirection.INBOUND)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        text = f"{ticket.subject}\n{message.body if message else ''}"
        order = await self._order_for(ctx, ticket, text)
        intent, confidence = classify(text)
        reasons = escalation_reasons(text, intent, confidence, order)
        if intent == "order_status" and order is None:
            reasons.append("couldn't find the order")
        output: dict[str, Any] = {
            "ticket_id": str(ticket.id),
            "intent": intent,
            "confidence": confidence,
            "order": order.name if order else None,
            "escalate": reasons,
        }
        decision = Decision(output=output)
        if reasons:
            return decision

        first = (change.data.get("customer_name") or "there").split(" ")[0]
        sender = ctx.settings.sender_name or ctx.shop.name
        if intent == "order_status":
            assert order is not None
            answer = _status_line(order)
        elif intent == "sizing":
            answer = SIZING_GUIDE
        else:
            answer = (
                "Happy to help with a return or exchange. Unworn items can be returned within 30 "
                "days. Reply with the item and the size you'd like instead, and we'll send a "
                "prepaid label." + (f" I've linked this to order {order.name}." if order else "")
            )
        body = f"Hi {first},\n\n{answer}\n\nThanks for shopping with us,\n{sender} support"

        if ctx.llm is not None:
            result = await ctx.llm.complete_json(
                agent=self.name,
                system=(
                    f"You are a friendly support agent for {sender}. Rewrite the draft reply so "
                    "it sounds natural. Keep every fact (order numbers, tracking, policies) "
                    f"exactly as given and never add new promises. Tone: {ctx.config.tone}."
                ),
                user=f"Customer wrote:\n{text}\n\nDraft reply:\n{body}",
                schema=Reply,
                prompt_version=PROMPT_VERSION,
                model=ctx.config.model,
                agent_run_id=ctx.run_id,
            )
            if result is None:
                decision.used_fallback = True
            else:
                body = result.value.body
                decision.model, decision.prompt_version = result.model, PROMPT_VERSION
                decision.cost_usd = float(result.cost_usd)

        subject = (
            ticket.subject if ticket.subject.lower().startswith("re:") else f"Re: {ticket.subject}"
        )
        decision.proposals.append(
            ProposalDraft(
                action_type="send_support_reply",
                title=f"Reply to {first}: {ticket.subject}",
                summary=answer,
                rationale=f"Recognised a {(intent or 'general').replace('_', ' ')} question "
                f"(confidence {confidence:.0%})"
                + (f" and found order {order.name}." if order else "."),
                risk_level=RiskLevel.LOW,
                payload={
                    "ticket_id": str(ticket.id),
                    "to": ticket.customer_email,
                    "subject": subject,
                    "text": body,
                    "resolve": intent in ("order_status", "sizing"),
                },
                preview={
                    "email": {"to": ticket.customer_email or "", "subject": subject, "body": body}
                },
                target_type="ticket",
                target_id=str(ticket.id),
                expires_in=timedelta(hours=12),
            )
        )
        return decision

    async def after_decide(self, ctx: AgentContext, change: Change, decision: Decision) -> None:
        reasons = decision.output.get("escalate") or []
        if not reasons:
            return
        ticket = await ctx.db.get(Ticket, uuid.UUID(decision.output["ticket_id"]))
        if ticket is None:
            return
        ticket.status = TicketStatus.ESCALATED
        ticket.priority = 1
        ticket.escalation_reason = "; ".join(reasons)[:255]
        ctx.db.add(
            Notification(
                shop_id=ctx.shop.id,
                kind="support.escalated",
                severity=Severity.WARNING,
                title=f"Needs a human: {ticket.subject}",
                body=f"Handed over because the {ticket.escalation_reason}.",
                data={"ticket_id": str(ticket.id)},
            )
        )
        await ctx.db.flush()
        await events.publish(
            ctx.redis,
            ctx.shop.id,
            "ticket.updated",
            {
                "id": str(ticket.id),
                "status": ticket.status.value,
                "reason": ticket.escalation_reason,
            },
        )
