import time

from redis.asyncio import Redis


async def hit(redis: Redis, bucket: str, limit: int, window_seconds: int = 60) -> bool:
    """Fixed-window counter. Returns True while the caller is within `limit` per window."""
    window = int(time.time() // window_seconds)
    key = f"ratelimit:{bucket}:{window}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds + 1)
        count, _ = await pipe.execute()
    return int(count) <= limit
