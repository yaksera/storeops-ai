"""Per-shop live event bus.

Each event is appended to a capped Redis stream (`shop:{id}:events`) — that gives every event a
monotonically increasing id that SSE clients echo back as `Last-Event-ID` to resume — and is then
fanned out on the pub/sub channel `shop:{id}` for connected dashboards.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings
from app.models.base import utcnow


def stream_key(shop_id: uuid.UUID | str) -> str:
    return f"shop:{shop_id}:events"


def channel(shop_id: uuid.UUID | str) -> str:
    return f"shop:{shop_id}"


@dataclass(frozen=True, slots=True)
class LiveEvent:
    id: str
    type: str
    data: dict[str, Any]
    at: str

    def to_json(self) -> str:
        return json.dumps({"id": self.id, "type": self.type, "data": self.data, "at": self.at})

    @classmethod
    def from_json(cls, raw: str) -> "LiveEvent":
        body = json.loads(raw)
        return cls(id=body["id"], type=body["type"], data=body["data"], at=body["at"])

    @classmethod
    def from_stream(cls, entry_id: str, fields: dict[str, str]) -> "LiveEvent":
        return cls(
            id=entry_id, type=fields["type"], data=json.loads(fields["data"]), at=fields["at"]
        )


def parse_id(event_id: str) -> tuple[int, int]:
    """Stream ids are `<ms>-<seq>`; compare them numerically."""
    ms, _, seq = event_id.partition("-")
    return int(ms), int(seq or 0)


def is_valid_id(event_id: str) -> bool:
    try:
        parse_id(event_id)
    except ValueError:
        return False
    return True


async def publish(
    redis: Redis, shop_id: uuid.UUID | str, event_type: str, data: dict[str, Any]
) -> LiveEvent:
    at = utcnow().isoformat()
    payload = json.dumps(data, default=str)
    entry_id = await redis.xadd(
        stream_key(shop_id),
        {"type": event_type, "data": payload, "at": at},
        maxlen=get_settings().event_stream_maxlen,
        approximate=True,
    )
    event = LiveEvent(id=str(entry_id), type=event_type, data=json.loads(payload), at=at)
    await redis.publish(channel(shop_id), event.to_json())
    return event


async def replay_after(
    redis: Redis, shop_id: uuid.UUID | str, last_id: str, limit: int = 500
) -> tuple[list[LiveEvent], bool]:
    """Events strictly after `last_id`. The bool is False when the id fell out of the capped stream
    (the client missed events and must reload its snapshot)."""
    oldest = await redis.xrange(stream_key(shop_id), count=1)
    complete = not (oldest and parse_id(oldest[0][0]) > parse_id(last_id))
    entries = await redis.xrange(stream_key(shop_id), min=f"({last_id}", count=limit)
    return [LiveEvent.from_stream(entry_id, fields) for entry_id, fields in entries], complete


async def recent(
    redis: Redis, shop_id: uuid.UUID | str, count: int = 50, types: set[str] | None = None
) -> list[LiveEvent]:
    """Newest first."""
    entries = await redis.xrevrange(stream_key(shop_id), count=count if types is None else 500)
    events = [LiveEvent.from_stream(entry_id, fields) for entry_id, fields in entries]
    if types is not None:
        events = [event for event in events if event.type in types][:count]
    return events


async def last_id(redis: Redis, shop_id: uuid.UUID | str) -> str:
    entries = await redis.xrevrange(stream_key(shop_id), count=1)
    return str(entries[0][0]) if entries else "0-0"
