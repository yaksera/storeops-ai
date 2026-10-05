"""Cart Recovery: personalised follow-up for checkouts abandoned past a threshold."""

import uuid
from datetime import timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, Decision, ProposalDraft
from app.models import Checkout, Customer
from app.models.enums import AgentName, CheckoutStatus, RiskLevel
from app.pipeline.normalize import Change

PROMPT_VERSION = "cart-recovery-v1"
MAX_REMINDERS = 2
DISCOUNT_VALID_HOURS = 48


class EmailCopy(BaseModel):
    subject: str = Field(max_length=120)
    opening: str = Field(max_length=600)


def _product_names(line_items: list[dict[str, Any]]) -> list[str]:
    names = []
    for li in line_items:
        title = str(li.get("title") or "your item")
        names.append(title.split(" - ")[0])
    return list(dict.fromkeys(names))


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else "your items"
    return ", ".join(names[:-1]) + " and " + names[-1]


def footer(ctx: AgentContext, checkout: Checkout) -> str:
    unsubscribe = f"https://{ctx.shop.domain}/pages/unsubscribe?token={checkout.token[:12]}"
    return (
        f"\n\n—\nYou're receiving this because you started a checkout at {ctx.shop.name}.\n"
        f"Unsubscribe: {unsubscribe}\n{ctx.settings.physical_address or ''}"
    )


class CartRecovery:
    name: ClassVar[AgentName] = AgentName.CART_RECOVERY
    triggers: ClassVar[frozenset[str]] = frozenset({"checkout.abandoned"})

    async def decide(self, ctx: AgentContext, change: Change) -> Decision | None:
        checkout = await ctx.db.get(Checkout, uuid.UUID(change.data["id"]))
        if checkout is None:
            return None
        customer = (
            await ctx.db.get(Customer, checkout.customer_id) if checkout.customer_id else None
        )
        output: dict[str, Any] = {
            "checkout_id": str(checkout.id),
            "reminders_sent": checkout.reminders_sent,
            "total_minor": checkout.total_minor,
        }
        decision = Decision(output=output)
        if checkout.status in (CheckoutStatus.COMPLETED, CheckoutStatus.RECOVERED):
            output["skipped"] = "checkout already completed"
            return decision
        if checkout.reminders_sent >= MAX_REMINDERS:
            output["skipped"] = "reminder limit reached"
            return decision
        if not checkout.email:
            output["skipped"] = "no email address"
            return decision
        if customer is None or not customer.accepts_marketing or customer.unsubscribed_at:
            output["skipped"] = "no marketing consent"
            return decision

        reminder = checkout.reminders_sent + 1
        names = _product_names(checkout.line_items)
        products = _join(names)
        first = customer.first_name or "there"
        pct = 0
        if reminder == MAX_REMINDERS:
            pct = min(
                int(ctx.config.settings.get("discount_pct", 10)), ctx.settings.max_discount_pct
            )
        sender = ctx.settings.sender_name or ctx.shop.name

        subject = (
            f"{first}, your {names[0] if names else 'cart'} is still waiting"
            if reminder == 1
            else f"{pct}% off to finish your order, {first}"
            if pct
            else f"Still thinking it over, {first}?"
        )
        opening = (
            f"Hi {first},\n\nYou left {products} in your cart at {sender}. "
            "We've saved everything for you, so you can pick up right where you left off."
        )
        if ctx.llm is not None:
            result = await ctx.llm.complete_json(
                agent=self.name,
                system=(
                    f"You write short, warm cart-recovery emails for {sender}, an outdoor gear "
                    f"brand. Tone: {ctx.config.tone}. Never invent discounts, prices or urgency."
                ),
                user=f"Customer first name: {first}. Products left in cart: {products}. "
                f"Reminder number {reminder} of {MAX_REMINDERS}.",
                schema=EmailCopy,
                prompt_version=PROMPT_VERSION,
                model=ctx.config.model,
                agent_run_id=ctx.run_id,
            )
            if result is None:
                decision.used_fallback = True
            else:
                subject, opening = result.value.subject, result.value.opening
                decision.model = result.model
                decision.prompt_version = PROMPT_VERSION
                decision.cost_usd = float(result.cost_usd)

        offer = (
            f"\n\nUse code {{discount_code}} for {pct}% off, "
            f"valid for {DISCOUNT_VALID_HOURS} hours."
            if pct
            else ""
        )
        text = (
            f"{opening}{offer}\n\nComplete your order: {checkout.recovery_url or ''}"
            f"\n\nHappy trails,\n{sender}{footer(ctx, checkout)}"
        )
        total = f"{checkout.total_minor / 100:,.2f} {checkout.currency}"
        output.update({"reminder": reminder, "discount_pct": pct})
        decision.proposals.append(
            ProposalDraft(
                action_type="send_recovery_email",
                title=f"Recovery email #{reminder} to {first} ({total})",
                summary=f"{first} left {products} ({total}) in their cart"
                + (
                    f". Includes a unique {pct}% code valid for {DISCOUNT_VALID_HOURS}h."
                    if pct
                    else "."
                ),
                rationale=f"Checkout abandoned with marketing consent; reminder {reminder} of "
                f"{MAX_REMINDERS}. Stops automatically if the order completes.",
                risk_level=RiskLevel.MEDIUM if pct else RiskLevel.LOW,
                payload={
                    "checkout_id": str(checkout.id),
                    "to": checkout.email,
                    "subject": subject,
                    "text": text,
                    "reminder": reminder,
                    "discount": {"pct": pct, "valid_hours": DISCOUNT_VALID_HOURS} if pct else None,
                },
                preview={"email": {"to": checkout.email, "subject": subject, "body": text}},
                target_type="checkout",
                target_id=str(checkout.id),
                expires_in=timedelta(hours=6),
                customer_facing=True,
                customer_id=customer.id,
            )
        )
        return decision

    async def after_decide(self, ctx: AgentContext, change: Change, decision: Decision) -> None:
        return None
