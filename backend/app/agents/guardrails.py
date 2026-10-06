"""Per-shop guardrails, checked when a proposal is created and again right before it executes."""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.models import AgentConfig, Customer, Shop, ShopSettings


@dataclass(slots=True)
class GuardrailResult:
    blocked: list[str] = field(default_factory=list)
    escalate: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocked


def in_quiet_hours(settings: ShopSettings, shop: Shop, now: datetime) -> bool:
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    if start is None or end is None or start == end:
        return False
    local: time = now.astimezone(ZoneInfo(shop.timezone)).time()
    if start < end:
        return start <= local < end
    return local >= start or local < end  # window wraps past midnight


def evaluate(
    *,
    shop: Shop,
    settings: ShopSettings,
    config: AgentConfig,
    payload: dict[str, Any],
    customer_facing: bool,
    customer: Customer | None,
    executed_today: int,
    now: datetime,
) -> GuardrailResult:
    result = GuardrailResult()
    if settings.kill_switch:
        result.blocked.append("Kill switch is on")
    if executed_today >= config.daily_action_cap:
        result.blocked.append(f"Daily cap of {config.daily_action_cap} actions reached")

    discount = payload.get("discount") or {}
    pct = discount.get("pct")
    if pct is not None and pct > settings.max_discount_pct:
        result.blocked.append(f"Discount {pct}% exceeds the {settings.max_discount_pct}% limit")

    refund = payload.get("refund_minor")
    if refund is not None and refund > settings.refund_ceiling_minor:
        result.escalate.append("Refund above the auto-approval ceiling")

    if customer_facing:
        if customer is None or customer.unsubscribed_at is not None:
            result.blocked.append("Customer has unsubscribed")
        elif not customer.accepts_marketing:
            result.blocked.append("Customer hasn't consented to marketing email")
        if not settings.physical_address:
            result.blocked.append("Set a physical mailing address before sending marketing email")
        if in_quiet_hours(settings, shop, now):
            result.escalate.append("Quiet hours: needs a human to send now")
    return result
