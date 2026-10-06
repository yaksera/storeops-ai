import logging
from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging
from app.core.observability import init_sentry, init_tracing
from app.core.queue import close_queue
from app.core.redis import close_redis
from app.worker import tasks

logger = logging.getLogger("storeops.worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    init_sentry(settings, "worker")
    init_tracing(settings, "worker")
    logger.info("worker starting", extra={"environment": settings.environment})


async def shutdown(ctx: dict[str, Any]) -> None:
    await close_queue()
    await close_redis()
    await dispose_engine()


class WorkerSettings:
    functions: ClassVar[list[Any]] = list(tasks.FUNCTIONS)
    cron_jobs: ClassVar[list[Any]] = list(tasks.CRON_JOBS)
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = get_settings().worker_queue
    max_jobs = 50
    job_timeout = 60
    keep_result = 60
