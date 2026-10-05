import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any

from arq import cron
from sqlalchemy import select, text

from app.agents import runner, scheduler
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.queue import get_queue
from app.core.redis import get_redis
from app.demo import simulator
from app.models import ActionProposal, Approval, Shop
from app.models.base import utcnow
from app.models.enums import ActorType, EventSource, ShopMode, ShopStatus
from app.pipeline.processor import process_event
from app.shopify import sync

logger = logging.getLogger("storeops.worker")

Task = Callable[..., Coroutine[Any, Any, Any]]

INITIAL_SYNC_DAYS = 90


async def ping(ctx: dict[str, Any]) -> str:
    """Smoke-test job: confirms the worker can reach the database."""
    async with get_sessionmaker()() as db:
        await db.execute(text("SELECT 1"))
    return "pong"


async def process_webhook_event(ctx: dict[str, Any], event_id: str) -> str | None:
    status = await process_event(get_sessionmaker(), get_redis(), uuid.UUID(event_id))
    return status.value if status else None


async def execute_approved_proposal(ctx: dict[str, Any], proposal_id: str) -> str | None:
    async with get_sessionmaker()() as db:
        proposal = await db.get(ActionProposal, uuid.UUID(proposal_id))
        if proposal is None:
            return None
        shop = await db.get(Shop, proposal.shop_id)
        if shop is None:
            return None
        actor = await db.scalar(
            select(Approval.user_id)
            .where(Approval.proposal_id == proposal.id)
            .order_by(Approval.decided_at.desc())
            .limit(1)
        )
        result = await runner.execute_proposal(
            db,
            get_redis(),
            shop,
            proposal,
            actor_type=ActorType.USER if actor else ActorType.SYSTEM,
            actor_id=str(actor) if actor else None,
        )
        return result.status.value


async def scan_abandoned_checkouts(ctx: dict[str, Any]) -> int:
    return await scheduler.scan_abandoned_checkouts(get_sessionmaker(), get_redis())


async def expire_proposals(ctx: dict[str, Any]) -> int:
    async with get_sessionmaker()() as db:
        return await runner.expire_proposals(db, get_redis())


async def initial_sync(ctx: dict[str, Any], shop_id: str) -> dict[str, int] | None:
    async with get_sessionmaker()() as db:
        shop = await db.get(Shop, uuid.UUID(shop_id))
        if shop is None:
            return None
        return await sync.sync_shop(
            db,
            get_redis(),
            get_queue(),
            shop,
            since=utcnow() - timedelta(days=INITIAL_SYNC_DAYS),
            source=EventSource.RECONCILIATION,
        )


async def reconcile_shops(ctx: dict[str, Any]) -> int:
    """Nightly: re-sync recent orders and products so missed webhooks are repaired."""
    async with get_sessionmaker()() as db:
        shops = (
            await db.scalars(
                select(Shop).where(
                    Shop.mode == ShopMode.LIVE,
                    Shop.status == ShopStatus.ACTIVE,
                    Shop.access_token_encrypted.is_not(None),
                )
            )
        ).all()
        done = 0
        for shop in shops:
            try:
                await sync.sync_shop(
                    db,
                    get_redis(),
                    get_queue(),
                    shop,
                    since=sync.reconciliation_window(),
                    source=EventSource.RECONCILIATION,
                )
                done += 1
            except Exception:
                logger.exception("reconciliation failed", extra={"shop": shop.domain})
        return done


async def simulator_tick(ctx: dict[str, Any]) -> int:
    if not get_settings().demo_mode_enabled:
        return 0
    return await simulator.simulate_tick(get_sessionmaker(), get_redis(), get_queue())


async def run_demo_scenario(ctx: dict[str, Any], shop_id: str, name: str) -> None:
    await simulator.run_scenario(
        get_sessionmaker(), get_redis(), get_queue(), uuid.UUID(shop_id), name
    )


def _tick_seconds() -> set[int]:
    step = max(1, round(get_settings().simulator_tick_seconds))
    return set(range(0, 60, step))


FUNCTIONS: list[Task] = [
    ping,
    process_webhook_event,
    run_demo_scenario,
    execute_approved_proposal,
    initial_sync,
]
CRON_JOBS: list[Any] = [
    cron(simulator_tick, second=_tick_seconds(), run_at_startup=False, unique=True, timeout=30),
    cron(scan_abandoned_checkouts, second={5, 35}, unique=True, timeout=55),
    cron(expire_proposals, second={50}, unique=True, timeout=55),
    cron(reconcile_shops, hour={9}, minute={15}, unique=True, timeout=3600),
]
