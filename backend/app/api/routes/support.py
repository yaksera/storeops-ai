import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents.effects import EffectsUnavailableError, effects_for
from app.api.deps import DbSession, RedisDep, ShopAdmin, ShopViewer
from app.models import ActionProposal, Message, Order, Review, Ticket
from app.models.base import utcnow
from app.models.enums import (
    ActorType,
    MessageAuthor,
    MessageDirection,
    ProposalStatus,
    TicketStatus,
)
from app.services import audit

router = APIRouter(prefix="/api/shops/{shop_id}/tickets", tags=["support"])

QUEUES: dict[str, list[TicketStatus]] = {
    "handover": [TicketStatus.ESCALATED],
    "open": [TicketStatus.OPEN, TicketStatus.PENDING],
    "resolved": [TicketStatus.RESOLVED, TicketStatus.CLOSED],
}


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    resolve: bool = True


def _ticket(t: Ticket, order: Order | None) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "subject": t.subject,
        "channel": t.channel.value,
        "status": t.status.value,
        "priority": t.priority,
        "customer_email": t.customer_email,
        "order_name": order.name if order else None,
        "escalation_reason": t.escalation_reason,
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        "created_at": t.created_at.isoformat(),
    }


@router.get("")
async def list_tickets(
    ctx: ShopViewer,
    db: DbSession,
    queue: Annotated[str, Query(pattern="^(handover|open|resolved)$")] = "handover",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(Ticket, Order)
            .outerjoin(Order, Order.id == Ticket.order_id)
            .where(Ticket.shop_id == ctx.shop.id, Ticket.status.in_(QUEUES[queue]))
            .order_by(Ticket.priority, Ticket.last_message_at.desc().nulls_last())
            .limit(limit)
        )
    ).all()
    counts = dict(
        (
            await db.execute(
                select(Ticket.status, func.count(Ticket.id))
                .where(Ticket.shop_id == ctx.shop.id)
                .group_by(Ticket.status)
            )
        ).all()
    )
    return {
        "items": [_ticket(t, o) for t, o in rows],
        "counts": {name: sum(counts.get(s, 0) for s in states) for name, states in QUEUES.items()},
    }


async def _load(db: DbSession, shop_id: uuid.UUID, ticket_id: uuid.UUID) -> Ticket:
    ticket = await db.scalar(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.shop_id == shop_id)
    )
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: uuid.UUID, ctx: ShopViewer, db: DbSession) -> dict[str, Any]:
    ticket = await _load(db, ctx.shop.id, ticket_id)
    order = await db.get(Order, ticket.order_id) if ticket.order_id else None
    review = await db.get(Review, ticket.review_id) if ticket.review_id else None
    messages = (
        await db.scalars(
            select(Message).where(Message.ticket_id == ticket.id).order_by(Message.created_at)
        )
    ).all()
    draft = await db.scalar(
        select(ActionProposal)
        .where(
            ActionProposal.shop_id == ctx.shop.id,
            ActionProposal.target_type == "ticket",
            ActionProposal.target_id == str(ticket.id),
            ActionProposal.status == ProposalStatus.PROPOSED,
        )
        .limit(1)
    )
    return {
        **_ticket(ticket, order),
        "order": (
            {
                "name": order.name,
                "total_minor": order.total_minor,
                "currency": order.currency,
                "fulfillment_status": order.fulfillment_status,
                "tracking_number": order.tracking_number,
                "is_held": order.is_held,
            }
            if order
            else None
        ),
        "review": (
            {"rating": review.rating, "title": review.title, "body": review.body}
            if review
            else None
        ),
        "messages": [
            {
                "id": str(m.id),
                "direction": m.direction.value,
                "author_type": m.author_type.value,
                "body": m.body,
                "sent_at": (m.sent_at or m.created_at).isoformat(),
            }
            for m in messages
        ],
        "draft": (
            {
                "proposal_id": str(draft.id),
                "text": draft.payload.get("text"),
                "summary": draft.summary,
            }
            if draft
            else None
        ),
    }


@router.post("/{ticket_id}/reply")
async def reply(
    ticket_id: uuid.UUID, body: ReplyBody, ctx: ShopAdmin, db: DbSession, redis: RedisDep
) -> dict[str, Any]:
    ticket = await _load(db, ctx.shop.id, ticket_id)
    if not ticket.customer_email:
        raise HTTPException(status.HTTP_409_CONFLICT, "This ticket has no customer email")
    subject = (
        ticket.subject if ticket.subject.lower().startswith("re:") else f"Re: {ticket.subject}"
    )
    try:
        message_id = await effects_for(ctx.shop, redis).send_email(
            to=ticket.customer_email, subject=subject, text=body.text, category="support"
        )
    except EffectsUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    now = utcnow()
    db.add(
        Message(
            shop_id=ctx.shop.id,
            ticket_id=ticket.id,
            direction=MessageDirection.OUTBOUND,
            author_type=MessageAuthor.HUMAN,
            author_user_id=ctx.user.id,
            body=body.text,
            external_id=message_id,
            sent_at=now,
        )
    )
    ticket.status = TicketStatus.RESOLVED if body.resolve else TicketStatus.PENDING
    ticket.last_message_at = now
    ticket.assignee_user_id = ctx.user.id
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="ticket.replied",
        target_type="ticket",
        target_id=ticket.id,
        details={"title": ticket.subject, "resolved": body.resolve},
    )
    await db.commit()
    return {"status": ticket.status.value}


@router.post("/{ticket_id}/resolve")
async def resolve(ticket_id: uuid.UUID, ctx: ShopAdmin, db: DbSession) -> dict[str, Any]:
    ticket = await _load(db, ctx.shop.id, ticket_id)
    ticket.status = TicketStatus.RESOLVED
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="ticket.resolved",
        target_type="ticket",
        target_id=ticket.id,
        details={"title": ticket.subject},
    )
    await db.commit()
    return {"status": ticket.status.value}
