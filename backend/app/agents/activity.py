"""Human-readable activity lines for the live dashboard."""

import uuid
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.models.enums import AgentName, Severity
from app.services import events

AGENT_LABELS: dict[AgentName, str] = {
    AgentName.ORCHESTRATOR: "Orchestrator",
    AgentName.FRAUD_GUARD: "Fraud Guard",
    AgentName.INVENTORY_PLANNER: "Inventory Planner",
    AgentName.CART_RECOVERY: "Cart Recovery",
    AgentName.SUPPORT: "Support Agent",
    AgentName.REVIEW_REPUTATION: "Review & Reputation",
    AgentName.REVENUE_ANALYST: "Revenue Analyst",
    AgentName.PRICING_ADVISOR: "Pricing Advisor",
}


def money(minor: int, currency: str) -> str:
    symbol = {"USD": "$", "CAD": "CA$", "EUR": "€", "GBP": "£", "AUD": "A$"}.get(currency)
    amount = f"{minor / 100:,.2f}"
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}"


@dataclass(frozen=True, slots=True)
class Activity:
    title: str
    detail: str
    severity: Severity = Severity.INFO


async def publish_activity(
    redis: Redis,
    shop_id: uuid.UUID,
    activity: Activity,
    *,
    kind: str,
    agent: AgentName | None = None,
    ref: dict[str, Any] | None = None,
) -> None:
    await events.publish(
        redis,
        shop_id,
        "activity",
        {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "agent": agent.value if agent else None,
            "title": activity.title,
            "detail": activity.detail,
            "severity": activity.severity.value,
            "ref": ref or {},
        },
    )
