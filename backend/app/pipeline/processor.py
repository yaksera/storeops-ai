import logging
import time
import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import orchestrator
from app.models import Shop, WebhookEvent
from app.models.base import utcnow
from app.models.enums import ShopStatus, WebhookStatus
from app.pipeline.normalize import normalize
from app.services import events, metrics

logger = logging.getLogger("storeops.pipeline")

KPI_TRIGGERS = {"order.created", "order.updated", "order.refunded", "checkout.created"}


async def process_event(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Redis, event_id: uuid.UUID
) -> WebhookStatus | None:
    """Normalise one stored webhook event, then fan it out. Safe to run more than once."""
    started = time.perf_counter()
    async with sessionmaker() as db:
        event = await db.get(WebhookEvent, event_id, with_for_update=True)
        if event is None:
            return None
        if event.status in (WebhookStatus.PROCESSED, WebhookStatus.SKIPPED):
            return event.status
        shop = await db.get(Shop, event.shop_id)
        if shop is None or shop.status != ShopStatus.ACTIVE:
            event.status = WebhookStatus.SKIPPED
            event.processed_at = utcnow()
            await db.commit()
            return event.status

        event.status = WebhookStatus.PROCESSING
        event.attempts += 1
        topic, payload = event.topic, event.payload
        try:
            result = await normalize(db, shop, topic, payload)
        except Exception as exc:
            await db.rollback()
            event = await db.get(WebhookEvent, event_id)
            assert event is not None
            event.status = WebhookStatus.FAILED
            event.attempts += 1
            event.error = f"{type(exc).__name__}: {exc}"[:2000]
            await db.commit()
            logger.exception("event processing failed", extra={"event_id": str(event_id)})
            return WebhookStatus.FAILED

        event.status = WebhookStatus.SKIPPED if result.stale else WebhookStatus.PROCESSED
        event.processed_at = utcnow()
        await db.commit()

        if result.changes:
            await orchestrator.route(db, redis, shop, result.changes)
            if any(change.kind in KPI_TRIGGERS for change in result.changes):
                kpis = await metrics.compute_kpis(db, shop)
                await events.publish(redis, shop.id, "kpis.updated", kpis.to_dict())

    logger.info(
        "event processed",
        extra={
            "event_id": str(event_id),
            "topic": topic,
            "status": event.status.value,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return event.status
