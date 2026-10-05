"""Server-side sessions stored in Redis.

The browser only holds an opaque random token in an httpOnly cookie; Redis stores the session
under the SHA-256 of that token, so a Redis dump does not leak usable cookies.
"""

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

_PREFIX = "session:"


@dataclass(frozen=True, slots=True)
class SessionData:
    user_id: uuid.UUID
    csrf_token: str


def _key(token: str) -> str:
    return _PREFIX + hashlib.sha256(token.encode()).hexdigest()


async def create_session(redis: Redis, user_id: uuid.UUID, ttl_seconds: int) -> tuple[str, str]:
    """Returns (session_token, csrf_token)."""
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": str(user_id), "csrf": csrf_token})
    await redis.set(_key(token), payload, ex=ttl_seconds)
    return token, csrf_token


async def load_session(redis: Redis, token: str) -> SessionData | None:
    raw = await redis.get(_key(token))
    if raw is None:
        return None
    data = json.loads(raw)
    return SessionData(user_id=uuid.UUID(data["user_id"]), csrf_token=data["csrf"])


async def destroy_session(redis: Redis, token: str) -> None:
    await redis.delete(_key(token))
