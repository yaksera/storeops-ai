"""Agent pipeline: decide → typed proposal → guardrails → approval (if required) → execute → audit.

Every agent invocation is isolated: an exception or timeout in one agent is recorded on its
`agent_runs` row and never prevents other agents from handling the same event.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import guardrails
from app.agents.activity import AGENT_LABELS, Activity, publish_activity
from app.agents.base import Agent, AgentContext, Decision, ProposalDraft
from app.agents.cart_recovery import CartRecovery
from app.agents.effects import Effects, EffectsUnavailableError, effects_for
from app.agents.fraud import FraudGuard
from app.agents.inventory import InventoryPlanner
from app.agents.pricing import PricingAdvisor
from app.agents.revenue import RevenueAnalyst
from app.agents.reviews import ReviewReputation
from app.agents.support import SupportAgent
from app.core.config import get_settings
from app.llm.client import LlmClient
from app.models import (
    ActionProposal,
    AgentConfig,
    AgentRun,
    Approval,
    Checkout,
    Customer,
    Message,
    Notification,
    Order,
    Product,
    Review,
    Shop,
    ShopSettings,
    Ticket,
    User,
    Variant,
)
from app.models.base import utcnow
from app.models.enums import (
    ActorType,
    AgentName,
    ApprovalDecision,
    Autonomy,
    CheckoutStatus,
    MessageAuthor,
    MessageDirection,
    ProposalStatus,
    RiskLevel,
    RunStatus,
    Severity,
    ShopMode,
    TicketStatus,
)
from app.pipeline.normalize import Change, order_summary
from app.services import audit, billing, events, metrics

logger = logging.getLogger("storeops.agents")

REGISTRY: dict[AgentName, Agent] = {
    agent.name: agent
    for agent in (
        FraudGuard(),
        InventoryPlanner(),
        CartRecovery(),
        SupportAgent(),
        ReviewReputation(),
        RevenueAnalyst(),
        PricingAdvisor(),
    )
}

# Fields a reviewer may change when approving, per action type.
EDITABLE_FIELDS: dict[str, set[str]] = {
    "send_recovery_email": {"subject", "text"},
    "draft_po": {"quantity", "to", "subject", "body"},
    "hold_order": set(),
    "send_support_reply": {"subject", "text"},
    "reply_to_review": {"reply"},
    "change_price": {"to_minor"},
}


class ActionSkippedError(Exception):
    """The action is no longer relevant (e.g. the cart was bought in the meantime)."""


def proposal_dict(p: ActionProposal) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "agent": p.agent.value,
        "agent_label": AGENT_LABELS[p.agent],
        "action_type": p.action_type,
        "title": p.title,
        "summary": p.summary,
        "rationale": p.rationale,
        "risk_level": p.risk_level.value,
        "requires_approval": p.requires_approval,
        "status": p.status.value,
        "target_type": p.target_type,
        "target_id": p.target_id,
        "payload": p.payload,
        "preview": p.preview,
        "result": p.result,
        "error": p.error,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "executed_at": p.executed_at.isoformat() if p.executed_at else None,
    }


async def _load(
    db: AsyncSession, shop_id: uuid.UUID, agent: AgentName
) -> tuple[ShopSettings, AgentConfig]:
    settings = (
        await db.execute(select(ShopSettings).where(ShopSettings.shop_id == shop_id))
    ).scalar_one()
    config = (
        await db.execute(
            select(AgentConfig).where(AgentConfig.shop_id == shop_id, AgentConfig.agent == agent)
        )
    ).scalar_one()
    return settings, config


async def executed_today(db: AsyncSession, shop: Shop, agent: AgentName, now: datetime) -> int:
    return int(
        await db.scalar(
            select(func.count(ActionProposal.id)).where(
                ActionProposal.shop_id == shop.id,
                ActionProposal.agent == agent,
                ActionProposal.status == ProposalStatus.EXECUTED,
                ActionProposal.executed_at >= metrics.day_start(shop, now),
            )
        )
        or 0
    )


async def _llm_for(db: AsyncSession, shop: Shop, now: datetime) -> LlmClient | None:
    settings = get_settings()
    # Demo stores never call external services; over-budget stores fall back to rules.
    if shop.mode == ShopMode.DEMO or settings.openrouter_api_key is None:
        return None
    if not await billing.llm_budget_left(db, shop, now):
        return None
    return LlmClient(settings, db, shop.id)


async def run_agent(
    db: AsyncSession, redis: Redis, shop: Shop, agent_name: AgentName, change: Change
) -> AgentRun | None:
    agent = REGISTRY.get(agent_name)
    if agent is None or change.kind not in agent.triggers:
        return None
    settings, config = await _load(db, shop.id, agent_name)
    if not config.enabled or config.autonomy == Autonomy.OFF:
        return None
    now = utcnow()
    run = AgentRun(
        id=uuid.uuid4(),
        shop_id=shop.id,
        agent=agent_name,
        trigger_topic=change.kind,
        inputs={"kind": change.kind, "data": change.data},
        started_at=now,
    )
    if settings.kill_switch:
        run.status = RunStatus.SKIPPED
        run.output = {"skipped": "kill switch is on"}
        run.finished_at, run.duration_ms = now, 0
        db.add(run)
        await db.commit()
        return run

    llm = await _llm_for(db, shop, now)
    ctx = AgentContext(
        db=db,
        redis=redis,
        shop=shop,
        settings=settings,
        config=config,
        now=now,
        llm=llm,
        run_id=run.id,
    )
    started = time.perf_counter()
    try:
        db.add(run)
        await db.flush()
        decision = await asyncio.wait_for(
            agent.decide(ctx, change), timeout=get_settings().agent_timeout_seconds
        )
        if decision is None:
            await db.rollback()
            await db.refresh(shop)
            return None
        await agent.after_decide(ctx, change, decision)
        _finish_run(run, decision, started)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await db.refresh(shop)
        logger.exception("agent failed", extra={"agent": agent_name.value, "shop_id": str(shop.id)})
        run = AgentRun(
            shop_id=shop.id,
            agent=agent_name,
            trigger_topic=change.kind,
            inputs={"kind": change.kind, "data": change.data},
            status=RunStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}"[:2000],
            started_at=now,
            finished_at=utcnow(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        db.add(run)
        await db.commit()
        await publish_activity(
            redis,
            shop.id,
            Activity(
                f"{AGENT_LABELS[agent_name]} hit an error",
                "Logged; other agents unaffected.",
                Severity.WARNING,
            ),
            kind="agent.error",
            agent=agent_name,
        )
        return run
    finally:
        if llm is not None:
            await llm.aclose()

    for draft in decision.proposals:
        await create_proposal(db, redis, shop, settings, config, run, draft)
    return run


def _finish_run(run: AgentRun, decision: Decision, started: float) -> None:
    run.status = RunStatus.SUCCEEDED
    run.output = {**decision.output, "proposals": len(decision.proposals)}
    run.model = decision.model
    run.prompt_version = decision.prompt_version
    run.used_fallback = decision.used_fallback
    run.cost_usd = Decimal(str(decision.cost_usd))
    run.finished_at = utcnow()
    run.duration_ms = int((time.perf_counter() - started) * 1000)


async def create_proposal(
    db: AsyncSession,
    redis: Redis,
    shop: Shop,
    settings: ShopSettings,
    config: AgentConfig,
    run: AgentRun | None,
    draft: ProposalDraft,
) -> ActionProposal:
    now = utcnow()
    customer = await db.get(Customer, draft.customer_id) if draft.customer_id else None
    check = guardrails.evaluate(
        shop=shop,
        settings=settings,
        config=config,
        payload=draft.payload,
        customer_facing=draft.customer_facing,
        customer=customer,
        executed_today=await executed_today(db, shop, config.agent, now),
        now=now,
        plan_limit=await billing.action_limit_reason(db, shop, now),
    )
    requires_approval = (
        draft.always_requires_approval
        or config.autonomy == Autonomy.SUGGEST
        or draft.risk_level == RiskLevel.HIGH
        or bool(check.escalate)
    )
    proposal = ActionProposal(
        id=uuid.uuid4(),
        shop_id=shop.id,
        agent_run_id=run.id if run else None,
        agent=config.agent,
        action_type=draft.action_type,
        title=draft.title,
        summary=draft.summary,
        rationale=draft.rationale + (" " + " ".join(check.escalate) if check.escalate else ""),
        risk_level=draft.risk_level,
        requires_approval=requires_approval,
        target_type=draft.target_type,
        target_id=draft.target_id,
        payload={
            **draft.payload,
            "_customer_facing": draft.customer_facing,
            "_customer_id": str(draft.customer_id) if draft.customer_id else None,
        },
        preview=draft.preview,
        status=ProposalStatus.PROPOSED,
        expires_at=now + draft.expires_in,
    )
    db.add(proposal)
    if not check.ok:
        proposal.status = ProposalStatus.REJECTED
        proposal.error = "Blocked by guardrails: " + "; ".join(check.blocked)
        audit.record(
            db,
            shop_id=shop.id,
            actor_type=ActorType.AGENT,
            actor_id=config.agent.value,
            action="guardrail.blocked",
            target_type="proposal",
            target_id=proposal.id,
            details={"title": draft.title, "reasons": check.blocked},
        )
        await db.commit()
        await events.publish(redis, shop.id, "proposal.updated", proposal_dict(proposal))
        await publish_activity(
            redis,
            shop.id,
            Activity(f"Blocked: {draft.title}", "; ".join(check.blocked) + ".", Severity.WARNING),
            kind="guardrail.blocked",
            agent=config.agent,
            ref={"proposal_id": str(proposal.id)},
        )
        return proposal

    audit.record(
        db,
        shop_id=shop.id,
        actor_type=ActorType.AGENT,
        actor_id=config.agent.value,
        action="proposal.created",
        target_type="proposal",
        target_id=proposal.id,
        details={
            "title": draft.title,
            "action_type": draft.action_type,
            "risk_level": draft.risk_level.value,
            "requires_approval": requires_approval,
            "agent_run_id": str(run.id) if run else None,
        },
    )
    if requires_approval:
        db.add(
            Notification(
                shop_id=shop.id,
                kind="approval.needed",
                severity=Severity.WARNING if draft.risk_level != RiskLevel.LOW else Severity.INFO,
                title=f"Approval needed: {draft.title}",
                body=draft.summary,
                data={"proposal_id": str(proposal.id)},
            )
        )
    await db.commit()
    await events.publish(redis, shop.id, "proposal.created", proposal_dict(proposal))
    if requires_approval:
        await publish_activity(
            redis,
            shop.id,
            Activity(
                f"Needs approval: {draft.title}",
                draft.summary,
                Severity.WARNING if draft.risk_level != RiskLevel.LOW else Severity.INFO,
            ),
            kind="proposal.created",
            agent=config.agent,
            ref={"proposal_id": str(proposal.id)},
        )
        return proposal
    await execute_proposal(
        db, redis, shop, proposal, actor_type=ActorType.AGENT, actor_id=config.agent.value
    )
    return proposal


# ---- execution ----------------------------------------------------------------------------

Executor = Callable[[AsyncSession, Redis, Shop, ActionProposal, Effects], Awaitable[dict[str, Any]]]


async def _hold_order(
    db: AsyncSession, redis: Redis, shop: Shop, proposal: ActionProposal, effects: Effects
) -> dict[str, Any]:
    order = await db.get(Order, uuid.UUID(proposal.payload["order_id"]))
    if order is None:
        raise ActionSkippedError("Order no longer exists")
    if order.cancelled_at is not None:
        raise ActionSkippedError("Order was cancelled")
    if order.fulfillment_status == "fulfilled":
        raise ActionSkippedError("Order already shipped")
    await effects.hold_order(order, proposal.summary)
    order.is_held = True
    await db.flush()
    customer = await db.get(Customer, order.customer_id) if order.customer_id else None
    await events.publish(redis, shop.id, "order.updated", order_summary(order, customer, 0))
    return {"held": True, "order": order.name}


async def _send_recovery_email(
    db: AsyncSession, redis: Redis, shop: Shop, proposal: ActionProposal, effects: Effects
) -> dict[str, Any]:
    checkout = await db.get(Checkout, uuid.UUID(proposal.payload["checkout_id"]))
    if checkout is None:
        raise ActionSkippedError("Checkout no longer exists")
    if checkout.status in (CheckoutStatus.COMPLETED, CheckoutStatus.RECOVERED):
        raise ActionSkippedError("Customer already completed the order")
    text = str(proposal.payload["text"])
    result: dict[str, Any] = {}
    discount = proposal.payload.get("discount")
    if discount:
        expires = utcnow() + timedelta(hours=int(discount.get("valid_hours", 48)))
        code = await effects.create_discount(pct=int(discount["pct"]), expires_at=expires)
        text = text.replace("{discount_code}", code)
        result["discount_code"] = code
        result["discount_expires_at"] = expires.isoformat()
    result["message_id"] = await effects.send_email(
        to=str(proposal.payload["to"]),
        subject=str(proposal.payload["subject"]),
        text=text,
        category="cart_recovery",
    )
    checkout.reminders_sent += 1
    checkout.last_reminder_at = utcnow()
    checkout.status = CheckoutStatus.ABANDONED
    result["reminder"] = checkout.reminders_sent
    return result


async def _send_po(
    db: AsyncSession, redis: Redis, shop: Shop, proposal: ActionProposal, effects: Effects
) -> dict[str, Any]:
    to = proposal.payload.get("to")
    if not to:
        raise EffectsUnavailableError("No supplier email configured for the Inventory Planner")
    message_id = await effects.send_email(
        to=str(to),
        subject=str(proposal.payload["subject"]),
        text=str(proposal.payload["body"]),
        category="purchase_order",
    )
    return {"message_id": message_id, "quantity": proposal.payload.get("quantity")}


async def _send_support_reply(
    db: AsyncSession, redis: Redis, shop: Shop, proposal: ActionProposal, effects: Effects
) -> dict[str, Any]:
    ticket = await db.get(Ticket, uuid.UUID(proposal.payload["ticket_id"]))
    if ticket is None:
        raise ActionSkippedError("Ticket no longer exists")
    if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        raise ActionSkippedError("Ticket was already resolved")
    if not proposal.payload.get("to"):
        raise ActionSkippedError("Customer has no email address")
    message_id = await effects.send_email(
        to=str(proposal.payload["to"]),
        subject=str(proposal.payload["subject"]),
        text=str(proposal.payload["text"]),
        category="support",
    )
    now = utcnow()
    db.add(
        Message(
            shop_id=shop.id,
            ticket_id=ticket.id,
            direction=MessageDirection.OUTBOUND,
            author_type=MessageAuthor.AGENT,
            body=str(proposal.payload["text"]),
            external_id=message_id,
            sent_at=now,
        )
    )
    ticket.status = (
        TicketStatus.RESOLVED if proposal.payload.get("resolve") else TicketStatus.PENDING
    )
    ticket.last_message_at = now
    await events.publish(
        redis, shop.id, "ticket.updated", {"id": str(ticket.id), "status": ticket.status.value}
    )
    return {"message_id": message_id, "ticket_status": ticket.status.value}


async def _reply_to_review(
    db: AsyncSession, redis: Redis, shop: Shop, proposal: ActionProposal, effects: Effects
) -> dict[str, Any]:
    review = await db.get(Review, uuid.UUID(proposal.payload["review_id"]))
    if review is None:
        raise ActionSkippedError("Review no longer exists")
    if review.replied_at is not None:
        raise ActionSkippedError("Review already has a reply")
    reply = str(proposal.payload["reply"])
    await effects.reply_to_review(review, reply)
    review.reply_draft = reply
    review.replied_at = utcnow()
    return {"replied": True}


async def _change_price(
    db: AsyncSession, redis: Redis, shop: Shop, proposal: ActionProposal, effects: Effects
) -> dict[str, Any]:
    variant = await db.get(Variant, uuid.UUID(proposal.payload["variant_id"]))
    if variant is None:
        raise ActionSkippedError("Variant no longer exists")
    to_minor = int(proposal.payload["to_minor"])
    floor = int(proposal.payload.get("floor_minor") or 0)
    if to_minor < floor:
        raise ValueError(f"New price is below the margin floor ({floor / 100:.2f})")
    previous = variant.price_minor
    product = await db.get(Product, variant.product_id)
    assert product is not None
    await effects.set_variant_price(product.shopify_id, variant, to_minor)
    variant.price_minor = to_minor
    return {"from_minor": previous, "to_minor": to_minor}


EXECUTORS: dict[str, Executor] = {
    "hold_order": _hold_order,
    "send_recovery_email": _send_recovery_email,
    "draft_po": _send_po,
    "send_support_reply": _send_support_reply,
    "reply_to_review": _reply_to_review,
    "change_price": _change_price,
}


async def execute_proposal(
    db: AsyncSession,
    redis: Redis,
    shop: Shop,
    proposal: ActionProposal,
    *,
    actor_type: ActorType,
    actor_id: str | None,
) -> ActionProposal:
    if proposal.status not in (ProposalStatus.PROPOSED, ProposalStatus.APPROVED):
        return proposal
    settings, config = await _load(db, shop.id, proposal.agent)
    now = utcnow()
    customer_id = proposal.payload.get("_customer_id")
    customer = await db.get(Customer, uuid.UUID(customer_id)) if customer_id else None
    check = guardrails.evaluate(
        shop=shop,
        settings=settings,
        config=config,
        payload=proposal.payload,
        customer_facing=bool(proposal.payload.get("_customer_facing")),
        customer=customer,
        executed_today=await executed_today(db, shop, proposal.agent, now),
        now=now,
        plan_limit=await billing.action_limit_reason(db, shop, now),
    )
    label = AGENT_LABELS[proposal.agent]
    if not check.ok:
        proposal.status = ProposalStatus.FAILED
        proposal.error = "Blocked by guardrails: " + "; ".join(check.blocked)
        action, severity = "guardrail.blocked", Severity.WARNING
        headline = f"Blocked: {proposal.title}"
        detail = "; ".join(check.blocked) + "."
    elif settings.dry_run:
        proposal.status = ProposalStatus.EXECUTED
        proposal.executed_at = now
        proposal.result = {"dry_run": True}
        action, severity = "action.dry_run", Severity.INFO
        headline = f"Dry run: {proposal.title}"
        detail = "Dry-run mode is on, so nothing was changed."
    else:
        try:
            result = await EXECUTORS[proposal.action_type](
                db, redis, shop, proposal, effects_for(shop, redis)
            )
            proposal.status = ProposalStatus.EXECUTED
            proposal.executed_at = now
            proposal.result = result
            await billing.increment(db, shop.id, billing.AI_ACTIONS, 1, now)
            await billing.maybe_alert(db, shop, now)
            action, severity = "action.executed", Severity.INFO
            headline = f"{label}: {proposal.title}"
            detail = proposal.summary
        except ActionSkippedError as exc:
            proposal.status = ProposalStatus.EXPIRED
            proposal.error = str(exc)
            action, severity = "action.skipped", Severity.INFO
            headline = f"Skipped: {proposal.title}"
            detail = f"{exc}."
        except Exception as exc:
            logger.exception("action failed", extra={"proposal_id": str(proposal.id)})
            proposal.status = ProposalStatus.FAILED
            proposal.error = f"{type(exc).__name__}: {exc}"[:2000]
            action, severity = "action.failed", Severity.CRITICAL
            headline = f"Failed: {proposal.title}"
            detail = str(exc) if isinstance(exc, EffectsUnavailableError) else "See the audit log."
    audit.record(
        db,
        shop_id=shop.id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type="proposal",
        target_id=proposal.id,
        details={
            "title": proposal.title,
            "action_type": proposal.action_type,
            "agent": proposal.agent.value,
            "result": proposal.result,
            "error": proposal.error,
        },
    )
    await db.commit()
    await events.publish(redis, shop.id, "proposal.updated", proposal_dict(proposal))
    await publish_activity(
        redis,
        shop.id,
        Activity(headline, detail, severity),
        kind=action,
        agent=proposal.agent,
        ref={"proposal_id": str(proposal.id)},
    )
    return proposal


# ---- human decisions ----------------------------------------------------------------------


class ProposalError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


async def decide_proposal(
    db: AsyncSession,
    redis: Redis,
    shop: Shop,
    proposal_id: uuid.UUID,
    user: User,
    decision: ApprovalDecision,
    *,
    edits: dict[str, Any] | None = None,
    comment: str | None = None,
) -> ActionProposal:
    proposal = await db.scalar(
        select(ActionProposal)
        .where(ActionProposal.id == proposal_id, ActionProposal.shop_id == shop.id)
        .with_for_update()
    )
    if proposal is None:
        raise ProposalError(404, "Proposal not found")
    if proposal.status != ProposalStatus.PROPOSED:
        raise ProposalError(409, f"Proposal is already {proposal.status.value}")
    if proposal.expires_at and proposal.expires_at < utcnow():
        proposal.status = ProposalStatus.EXPIRED
        await db.commit()
        raise ProposalError(409, "Proposal has expired")

    if edits:
        allowed = EDITABLE_FIELDS.get(proposal.action_type, set())
        unknown = set(edits) - allowed
        if unknown:
            raise ProposalError(422, f"Cannot edit: {', '.join(sorted(unknown))}")
        if "to_minor" in edits and (
            not isinstance(edits["to_minor"], int) or edits["to_minor"] <= 0
        ):
            raise ProposalError(422, "to_minor must be a positive integer")
        if "quantity" in edits and (
            not isinstance(edits["quantity"], int) or edits["quantity"] <= 0
        ):
            raise ProposalError(422, "quantity must be a positive integer")
        proposal.payload = {**proposal.payload, **edits}

    db.add(
        Approval(
            shop_id=shop.id,
            proposal_id=proposal.id,
            user_id=user.id,
            decision=decision,
            edited_payload=edits or None,
            comment=comment,
        )
    )
    proposal.status = (
        ProposalStatus.APPROVED
        if decision == ApprovalDecision.APPROVED
        else ProposalStatus.REJECTED
    )
    audit.record(
        db,
        shop_id=shop.id,
        actor_type=ActorType.USER,
        actor_id=user.id,
        action=f"proposal.{decision.value}",
        target_type="proposal",
        target_id=proposal.id,
        details={
            "title": proposal.title,
            "edited": sorted(edits) if edits else [],
            "comment": comment,
        },
    )
    await db.commit()
    await events.publish(redis, shop.id, "proposal.updated", proposal_dict(proposal))
    if decision == ApprovalDecision.REJECTED:
        await publish_activity(
            redis,
            shop.id,
            Activity(f"Rejected: {proposal.title}", f"Declined by {user.full_name or user.email}."),
            kind="proposal.rejected",
            agent=proposal.agent,
            ref={"proposal_id": str(proposal.id)},
        )
    return proposal


async def expire_proposals(db: AsyncSession, redis: Redis, now: datetime | None = None) -> int:
    now = now or utcnow()
    rows = (
        await db.execute(
            update(ActionProposal)
            .where(
                ActionProposal.status == ProposalStatus.PROPOSED, ActionProposal.expires_at < now
            )
            .values(status=ProposalStatus.EXPIRED, error="Expired without a decision")
            .returning(ActionProposal.id, ActionProposal.shop_id, ActionProposal.title)
        )
    ).all()
    for proposal_id, shop_id, title in rows:
        audit.record(
            db,
            shop_id=shop_id,
            actor_type=ActorType.SYSTEM,
            action="proposal.expired",
            target_type="proposal",
            target_id=proposal_id,
            details={"title": title},
        )
    await db.commit()
    for proposal_id, shop_id, _ in rows:
        await events.publish(
            redis, shop_id, "proposal.updated", {"id": str(proposal_id), "status": "expired"}
        )
    return len(rows)
