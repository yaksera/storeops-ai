import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ShopScoped, Timestamps, UUIDPrimaryKey, str_enum, utcnow
from app.models.enums import (
    ActorType,
    AgentName,
    ApprovalDecision,
    LlmCallStatus,
    ProposalStatus,
    RiskLevel,
    RunStatus,
)

Money6 = Numeric(12, 6)


class AgentRun(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    """One invocation of an agent's decide() for one trigger event."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_shop_agent_started", "shop_id", "agent", "started_at"),)

    agent: Mapped[AgentName] = mapped_column(str_enum(AgentName, "agent_name"))
    trigger_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("webhook_events.id", ondelete="SET NULL")
    )
    trigger_topic: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "run_status"), default=RunStatus.RUNNING
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[dict[str, Any] | None]
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    used_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[Decimal] = mapped_column(Money6, default=Decimal(0))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class ActionProposal(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "action_proposals"
    __table_args__ = (Index("ix_action_proposals_shop_status", "shop_id", "status"),)

    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    agent: Mapped[AgentName] = mapped_column(str_enum(AgentName, "agent_name"))
    action_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[RiskLevel] = mapped_column(str_enum(RiskLevel, "risk_level"))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    preview: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[ProposalStatus] = mapped_column(
        str_enum(ProposalStatus, "proposal_status"), default=ProposalStatus.PROPOSED
    )
    expires_at: Mapped[datetime | None]
    executed_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)


class Approval(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "approvals"

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("action_proposals.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision: Mapped[ApprovalDecision] = mapped_column(
        str_enum(ApprovalDecision, "approval_decision")
    )
    edited_payload: Mapped[dict[str, Any] | None]
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuditLog(ShopScoped, Base):
    """Append-only. UPDATE and DELETE are blocked by a database trigger (see migrations)."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_shop_occurred", "shop_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "actor_type"))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class LlmCall(UUIDPrimaryKey, ShopScoped, Base):
    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_shop_created", "shop_id", "created_at"),)

    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    agent: Mapped[AgentName] = mapped_column(str_enum(AgentName, "agent_name"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Money6, default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[LlmCallStatus] = mapped_column(str_enum(LlmCallStatus, "llm_call_status"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
