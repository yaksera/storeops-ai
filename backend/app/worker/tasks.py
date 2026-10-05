import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from arq import cron
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.queue import get_queue
from app.core.redis import get_redis
from app.demo import simulator
from app.pipeline.processor import process_event

logger = logging.getLogger("storeops.worker")

Task = Callable[..., Coroutine[Any, Any, Any]]


async def ping(ctx: dict[str, Any]) -> str:
    """Smoke-test job: confirms the worker can reach the database."""
    async with get_sessionmaker()() as db:
        await db.execute(text("SELECT 1"))
    return "pong"


async def process_webhook_event(ctx: dict[str, Any], event_id: str) -> str | None:
    status = await process_event(get_sessionmaker(), get_redis(), uuid.UUID(event_id))
    return status.value if status else None


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


FUNCTIONS: list[Task] = [ping, process_webhook_event, run_demo_scenario]
CRON_JOBS: list[Any] = [
    cron(simulator_tick, second=_tick_seconds(), run_at_startup=False, unique=True, timeout=30)
]
