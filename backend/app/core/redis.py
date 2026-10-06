from redis.asyncio import Redis

from app.core.config import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def set_redis(client: Redis | None) -> None:
    """Swap the shared client (used by tests to inject a fake)."""
    global _redis
    _redis = client


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None
