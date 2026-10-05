import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.client import LlmClient
from app.models import AgentConfig, Shop, ShopSettings
from app.models.enums import AgentName, RiskLevel
from app.pipeline.normalize import Change


@dataclass(slots=True)
class AgentContext:
    db: AsyncSession
    redis: Redis
    shop: Shop
    settings: ShopSettings
    config: AgentConfig
    now: datetime
    llm: LlmClient | None = None
    run_id: uuid.UUID | None = None


@dataclass(slots=True)
class ProposalDraft:
    """A typed action an agent wants to take. Guardrails and approval decide whether it runs."""

    action_type: str
    title: str
    summary: str
    rationale: str
    risk_level: RiskLevel
    payload: dict[str, Any]
    preview: dict[str, Any] = field(default_factory=dict)
    target_type: str | None = None
    target_id: str | None = None
    always_requires_approval: bool = False
    expires_in: timedelta = timedelta(hours=24)
    customer_facing: bool = False
    customer_id: uuid.UUID | None = None


@dataclass(slots=True)
class Decision:
    output: dict[str, Any]
    proposals: list[ProposalDraft] = field(default_factory=list)
    model: str | None = None
    prompt_version: str = "rules-v1"
    used_fallback: bool = False
    cost_usd: float = 0.0


class Agent(Protocol):
    name: ClassVar[AgentName]
    triggers: ClassVar[frozenset[str]]

    async def decide(self, ctx: AgentContext, change: Change) -> Decision | None:
        """Return None when the event needs no decision (nothing is recorded)."""
        ...

    async def after_decide(self, ctx: AgentContext, change: Change, decision: Decision) -> None:
        """Persist analysis that isn't an action (e.g. a risk score). Optional."""
        ...
