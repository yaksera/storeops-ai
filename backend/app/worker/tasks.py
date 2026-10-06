import logging
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy import text

from app.core.db import get_sessionmaker

logger = logging.getLogger("storeops.worker")

Task = Callable[..., Coroutine[Any, Any, Any]]


async def ping(ctx: dict[str, Any]) -> str:
    """Smoke-test job: confirms the worker can reach the database."""
    async with get_sessionmaker()() as db:
        await db.execute(text("SELECT 1"))
    return "pong"


FUNCTIONS: list[Task] = [ping]
CRON_JOBS: list[Any] = []
