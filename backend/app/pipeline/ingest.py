import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue import JobQueue
from app.models import WebhookEvent
from app.models.base import utcnow
from app.models.enums import EventSource, WebhookStatus

PROCESS_JOB = "process_webhook_event"


@dataclass(frozen=True, slots=True)
class IngestResult:
    event_id: uuid.UUID | None
    duplicate: bool


async def ingest_event(
    db: AsyncSession,
    queue: JobQueue,
    *,
    shop_id: uuid.UUID,
    topic: str,
    webhook_id: str,
    payload: dict[str, Any],
    source: EventSource,
    triggered_at: datetime | None = None,
) -> IngestResult:
    """Persist the raw event (deduplicated on `webhook_id`) and enqueue processing.

    This is the only work done on the request path, so acknowledging stays well under a second.
    """
    event_id = uuid.uuid4()
    inserted = await db.scalar(
        insert(WebhookEvent)
        .values(
            id=event_id,
            shop_id=shop_id,
            webhook_id=webhook_id,
            topic=topic,
            source=source,
            payload=payload,
            status=WebhookStatus.RECEIVED,
            triggered_at=triggered_at,
            received_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=[WebhookEvent.webhook_id])
        .returning(WebhookEvent.id)
    )
    if inserted is None:
        return IngestResult(event_id=None, duplicate=True)
    await db.commit()
    # Job id = event id, so a retried enqueue can't create a second job for the same event.
    await queue.enqueue(PROCESS_JOB, str(event_id), job_id=f"wh:{event_id}")
    await db.execute(
        update(WebhookEvent)
        .where(WebhookEvent.id == event_id, WebhookEvent.status == WebhookStatus.RECEIVED)
        .values(status=WebhookStatus.QUEUED)
    )
    await db.commit()
    return IngestResult(event_id=event_id, duplicate=False)
