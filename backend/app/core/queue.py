"""Job queue facade so the API and services don't depend on Arq directly (and tests can run jobs
inline)."""

from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings


class JobQueue(Protocol):
    async def enqueue(
        self, function: str, *args: Any, job_id: str | None = None, priority: bool = False
    ) -> None: ...


class ArqQueue:
    def __init__(self) -> None:
        self._pool: ArqRedis | None = None

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        return self._pool

    async def enqueue(
        self, function: str, *args: Any, job_id: str | None = None, priority: bool = False
    ) -> None:
        pool = await self._get_pool()
        queue = get_settings().priority_queue if priority else None
        await pool.enqueue_job(function, *args, _job_id=job_id, _queue_name=queue)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
        self._pool = None


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    global _queue
    if _queue is None:
        _queue = ArqQueue()
    return _queue


def set_queue(queue: JobQueue | None) -> None:
    global _queue
    _queue = queue


async def close_queue() -> None:
    global _queue
    if isinstance(_queue, ArqQueue):
        await _queue.close()
    _queue = None
