import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents.runner import ProposalError, decide_proposal, proposal_dict
from app.api.deps import DbSession, RedisDep, ShopAdmin, ShopViewer
from app.core.queue import get_queue
from app.models import ActionProposal
from app.models.enums import AgentName, ApprovalDecision, ProposalStatus

router = APIRouter(prefix="/api/shops/{shop_id}/proposals", tags=["approvals"])

EXECUTE_JOB = "execute_approved_proposal"


class DecisionBody(BaseModel):
    edits: dict[str, Any] | None = None
    comment: str | None = Field(default=None, max_length=1000)


class BulkApproveBody(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


@router.get("")
async def list_proposals(
    ctx: ShopViewer,
    db: DbSession,
    status_filter: Annotated[ProposalStatus | None, Query(alias="status")] = None,
    agent: AgentName | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    query = select(ActionProposal).where(ActionProposal.shop_id == ctx.shop.id)
    if status_filter is not None:
        query = query.where(ActionProposal.status == status_filter)
    if agent is not None:
        query = query.where(ActionProposal.agent == agent)
    rows = (await db.scalars(query.order_by(ActionProposal.created_at.desc()).limit(limit))).all()
    pending = await db.scalar(
        select(func.count(ActionProposal.id)).where(
            ActionProposal.shop_id == ctx.shop.id,
            ActionProposal.status == ProposalStatus.PROPOSED,
        )
    )
    return {"items": [proposal_dict(p) for p in rows], "pending": int(pending or 0)}


async def _decide(
    ctx: ShopAdmin,
    db: DbSession,
    redis: RedisDep,
    proposal_id: uuid.UUID,
    decision: ApprovalDecision,
    body: DecisionBody,
) -> dict[str, Any]:
    try:
        proposal = await decide_proposal(
            db,
            redis,
            ctx.shop,
            proposal_id,
            ctx.user,
            decision,
            edits=body.edits,
            comment=body.comment,
        )
    except ProposalError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    if decision == ApprovalDecision.APPROVED:
        await get_queue().enqueue(EXECUTE_JOB, str(proposal.id), job_id=f"exec:{proposal.id}")
        await db.refresh(proposal)
    return proposal_dict(proposal)


@router.post("/{proposal_id}/approve")
async def approve(
    proposal_id: uuid.UUID,
    ctx: ShopAdmin,
    db: DbSession,
    redis: RedisDep,
    body: DecisionBody | None = None,
) -> dict[str, Any]:
    return await _decide(
        ctx, db, redis, proposal_id, ApprovalDecision.APPROVED, body or DecisionBody()
    )


@router.post("/{proposal_id}/reject")
async def reject(
    proposal_id: uuid.UUID,
    ctx: ShopAdmin,
    db: DbSession,
    redis: RedisDep,
    body: DecisionBody | None = None,
) -> dict[str, Any]:
    payload = body or DecisionBody()
    if payload.edits:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Edits only apply to approvals")
    return await _decide(ctx, db, redis, proposal_id, ApprovalDecision.REJECTED, payload)


@router.post("/bulk-approve")
async def bulk_approve(
    body: BulkApproveBody, ctx: ShopAdmin, db: DbSession, redis: RedisDep
) -> dict[str, Any]:
    approved: list[str] = []
    errors: dict[str, str] = {}
    for proposal_id in dict.fromkeys(body.ids):
        try:
            result = await _decide(
                ctx, db, redis, proposal_id, ApprovalDecision.APPROVED, DecisionBody()
            )
            approved.append(result["id"])
        except HTTPException as exc:
            errors[str(proposal_id)] = str(exc.detail)
    return {"approved": approved, "errors": errors}
