"""Orchestrator: routes domain changes to the agents subscribed to them and narrates what is
happening for the live dashboard.

Agents' decide()/execute() pipelines plug in behind `route()`; until then the orchestrator keeps
each agent's live status (what it is looking at right now) and the activity stream.
"""

import json
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity import AGENT_LABELS, Activity, money, publish_activity
from app.agents.runner import run_agent
from app.models import AgentConfig, Shop
from app.models.base import utcnow
from app.models.enums import AgentName, Autonomy, Severity
from app.pipeline.normalize import Change
from app.services import events

# Which agents care about which domain changes, and how to describe the work they pick up.
ROUTES: dict[str, list[tuple[AgentName, str]]] = {
    "order.created": [
        (AgentName.FRAUD_GUARD, "Scoring order {name}"),
        (AgentName.REVENUE_ANALYST, "Updating sales pulse"),
    ],
    "order.refunded": [(AgentName.REVENUE_ANALYST, "Checking refund rate after {name}")],
    "inventory.changed": [
        (AgentName.INVENTORY_PLANNER, "Re-forecasting {sku}"),
        (AgentName.PRICING_ADVISOR, "Reviewing sell-through for {product}"),
    ],
    "checkout.created": [(AgentName.CART_RECOVERY, "Watching a {total} checkout")],
    "checkout.abandoned": [(AgentName.CART_RECOVERY, "Drafting a follow-up for a {total} cart")],
    "review.created": [(AgentName.REVIEW_REPUTATION, "Reading a {rating}★ review")],
    "ticket.created": [(AgentName.SUPPORT, "Triaging “{subject}”")],
}


def _agents_key(shop_id: uuid.UUID) -> str:
    return f"shop:{shop_id}:agents"


def describe(change: Change, routed: list[AgentName]) -> Activity | None:
    """Human-readable line for the activity stream, including *why* it matters."""
    d = change.data
    handoff = "Routed to " + " and ".join(AGENT_LABELS[a] for a in routed) + "." if routed else ""
    match change.kind:
        case "order.created":
            who = d.get("customer_name") or "A customer"
            where = f" from {d['shipping_country']}" if d.get("shipping_country") else ""
            via = f" via {d['source']}" if d.get("source") else ""
            items = d.get("item_count") or 0
            return Activity(
                f"New order {d['name']} · {money(d['total_minor'], d['currency'])}",
                f"{who}{where}, {items} item{'s' if items != 1 else ''}{via}. {handoff}".strip(),
            )
        case "order.refunded":
            return Activity(
                f"Refund on {d['name']} · {money(d['refund_minor'], d['currency'])}",
                f"Order is now {d['financial_status'].replace('_', ' ')}. {handoff}".strip(),
                Severity.WARNING,
            )
        case "order.fulfilled":
            return Activity(f"{d['name']} shipped", "Fulfillment created; tracking sent.")
        case "order.updated" if d.get("cancelled"):
            return Activity(
                f"{d['name']} cancelled", "Order cancelled in Shopify.", Severity.WARNING
            )
        case "inventory.changed":
            label = f"{d['product']} / {d['variant']}"
            if d["delta"] > 0:
                return Activity(
                    f"Restocked {label}", f"+{d['delta']} units, {d['quantity']} on hand."
                )
            if d["quantity"] <= 0:
                return Activity(
                    f"Out of stock: {label}",
                    f"Sold through the last units. {handoff}".strip(),
                    Severity.CRITICAL,
                )
            if d.get("low"):
                return Activity(
                    f"Low stock: {label} ({d['quantity']} left)",
                    f"Below the reorder point of {d['reorder_point']}. {handoff}".strip(),
                    Severity.WARNING,
                )
            return None
        case "checkout.created":
            return Activity(
                f"Checkout started · {money(d['total_minor'], d['currency'])}",
                f"{d.get('customer_name') or 'A shopper'} has {d['item_count']} item(s) in cart. "
                f"{handoff}".strip(),
            )
        case "checkout.abandoned":
            return Activity(
                f"Checkout abandoned · {money(d['total_minor'], d['currency'])}",
                f"{d.get('customer_name') or 'A shopper'} left {d['item_count']} item(s) behind. "
                f"{handoff}".strip(),
            )
        case "review.created":
            low = d["rating"] <= 2
            product = f" on {d['product']}" if d.get("product") else ""
            return Activity(
                f"{d['rating']}★ review{product}",
                f"“{d.get('title') or d['body'][:80]}” by {d.get('author') or 'a customer'}. "
                f"{handoff}".strip(),
                Severity.WARNING if low else Severity.INFO,
            )
        case "ticket.created":
            order = f" about {d['order_name']}" if d.get("order_name") else ""
            return Activity(
                f"Support email: {d['subject']}",
                f"From {d.get('customer_name') or 'a customer'}{order}. {handoff}".strip(),
            )
    return None


async def set_agent_status(
    redis: Redis, shop_id: uuid.UUID, agent: AgentName, status: str, task: str | None
) -> dict[str, Any]:
    state = {"agent": agent.value, "status": status, "task": task, "at": utcnow().isoformat()}
    await redis.hset(_agents_key(shop_id), agent.value, json.dumps(state))  # type: ignore[misc]
    await events.publish(redis, shop_id, "agent.status", state)
    return state


async def agent_states(redis: Redis, shop_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    raw: dict[str, str] = await redis.hgetall(_agents_key(shop_id))  # type: ignore[misc]
    return {agent: json.loads(value) for agent, value in raw.items()}


async def enabled_agents(db: AsyncSession, shop_id: uuid.UUID) -> set[AgentName]:
    rows = await db.scalars(
        select(AgentConfig.agent).where(
            AgentConfig.shop_id == shop_id,
            AgentConfig.enabled.is_(True),
            AgentConfig.autonomy != Autonomy.OFF,
        )
    )
    return set(rows.all())


def _format_task(template: str, data: dict[str, Any]) -> str:
    values = dict(data)
    if "total_minor" in data and "currency" in data:
        values["total"] = money(data["total_minor"], data["currency"])
    try:
        return template.format(**values)
    except (KeyError, IndexError):
        return template.split("{")[0].strip()


async def route(
    db: AsyncSession, redis: Redis, shop: Shop, changes: list[Change]
) -> list[tuple[AgentName, Change]]:
    """Publish each change, hand it to subscribed agents and narrate it. Returns the dispatches."""
    enabled = await enabled_agents(db, shop.id)
    dispatched: list[tuple[AgentName, Change]] = []
    for change in changes:
        await events.publish(redis, shop.id, change.kind, change.data)
        targets = [agent for agent, _ in ROUTES.get(change.kind, []) if agent in enabled]
        for agent, template in ROUTES.get(change.kind, []):
            if agent in enabled:
                await set_agent_status(
                    redis, shop.id, agent, "working", _format_task(template, change.data)
                )
                dispatched.append((agent, change))
        activity = describe(change, targets)
        if activity is not None:
            await publish_activity(
                redis, shop.id, activity, kind=change.kind, ref={"id": change.data.get("id")}
            )
    # Agents run after the dashboard has been told about the change, each in isolation.
    for agent, change in dispatched:
        await run_agent(db, redis, shop, agent, change)
    return dispatched
