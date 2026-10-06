import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.agents.orchestrator import AGENT_LABELS, agent_states
from app.api.deps import DbSession, RedisDep, SettingsDep, ShopAdmin, ShopViewer
from app.core.queue import get_queue
from app.demo import simulator
from app.models import AgentConfig, AgentRun, Customer, Order, OrderItem
from app.models.base import utcnow
from app.models.enums import ActorType, AgentName, ShopMode
from app.pipeline.normalize import order_summary
from app.services import audit, events, metrics

logger = logging.getLogger("storeops.live")

router = APIRouter(prefix="/api/shops/{shop_id}", tags=["live"])

ACTIVITY_TYPES = {"activity"}


class AgentOut(BaseModel):
    agent: AgentName
    label: str
    enabled: bool
    autonomy: str
    status: str
    task: str | None
    last_active_at: str | None
    actions_today: int


class EventOut(BaseModel):
    id: str
    type: str
    data: dict[str, Any]
    at: str


class Dashboard(BaseModel):
    kpis: dict[str, Any]
    revenue_by_hour: dict[str, list[int]]
    orders: list[dict[str, Any]]
    agents: list[AgentOut]
    activity: list[EventOut]
    last_event_id: str
    simulator: dict[str, Any] | None


async def _agents(ctx: ShopViewer, db: DbSession, redis: Redis) -> list[AgentOut]:
    configs = (
        await db.scalars(select(AgentConfig).where(AgentConfig.shop_id == ctx.shop.id))
    ).all()
    start = metrics.day_start(ctx.shop, utcnow())
    counts = dict(
        (
            await db.execute(
                select(AgentRun.agent, func.count(AgentRun.id))
                .where(AgentRun.shop_id == ctx.shop.id, AgentRun.started_at >= start)
                .group_by(AgentRun.agent)
            )
        ).all()
    )
    live = await agent_states(redis, ctx.shop.id)
    order = list(AgentName)
    result = []
    for config in sorted(configs, key=lambda c: order.index(c.agent)):
        state = live.get(config.agent.value, {})
        enabled = config.enabled and config.autonomy.value != "off"
        result.append(
            AgentOut(
                agent=config.agent,
                label=AGENT_LABELS[config.agent],
                enabled=enabled,
                autonomy=config.autonomy.value,
                status=state.get("status", "watching") if enabled else "off",
                task=state.get("task") if enabled else None,
                last_active_at=state.get("at"),
                actions_today=int(counts.get(config.agent, 0)),
            )
        )
    return result


async def _recent_orders(ctx: ShopViewer, db: DbSession, limit: int = 25) -> list[dict[str, Any]]:
    item_count = (
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .where(OrderItem.order_id == Order.id)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(Order, Customer, item_count)
            .outerjoin(Customer, Customer.id == Order.customer_id)
            .where(Order.shop_id == ctx.shop.id)
            .order_by(Order.processed_at.desc())
            .limit(limit)
        )
    ).all()
    return [order_summary(order, customer, int(count)) for order, customer, count in rows]


@router.get("/dashboard")
async def dashboard(ctx: ShopViewer, db: DbSession, redis: RedisDep) -> Dashboard:
    """Snapshot for the Live Ops screen. Clients then resume the stream from `last_event_id`."""
    last_id = await events.last_id(redis, ctx.shop.id)
    kpis = await metrics.compute_kpis(db, ctx.shop)
    sim: dict[str, Any] | None = None
    if ctx.shop.mode == ShopMode.DEMO:
        await simulator.mark_watched(redis, ctx.shop.id)
        sim = {
            "running": not await simulator.is_paused(redis, ctx.shop.id),
            "scenarios": [{"id": k, "label": v} for k, v in simulator.SCENARIOS.items()],
        }
    activity = await events.recent(redis, ctx.shop.id, 40, ACTIVITY_TYPES)
    return Dashboard(
        kpis=kpis.to_dict(),
        revenue_by_hour=await metrics.revenue_by_hour(db, ctx.shop),
        orders=await _recent_orders(ctx, db),
        agents=await _agents(ctx, db, redis),
        activity=[EventOut(id=e.id, type=e.type, data=e.data, at=e.at) for e in activity],
        last_event_id=last_id,
        simulator=sim,
    )


@router.get("/events")
async def poll_events(
    ctx: ShopViewer,
    redis: RedisDep,
    after: Annotated[str, Query(max_length=64)] = "0-0",
) -> dict[str, Any]:
    """Polling fallback for clients that cannot hold an SSE connection."""
    if not events.is_valid_id(after):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid event id")
    if ctx.shop.mode == ShopMode.DEMO:
        await simulator.mark_watched(redis, ctx.shop.id)
    replay, complete = await events.replay_after(redis, ctx.shop.id, after)
    return {
        "events": [EventOut(id=e.id, type=e.type, data=e.data, at=e.at) for e in replay],
        "reset": not complete,
    }


def _sse(event: events.LiveEvent) -> str:
    return f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event.data)}\n\n"


@router.get("/events/stream")
async def stream_events(
    request: Request,
    ctx: ShopViewer,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    last_event_id: Annotated[str | None, Header()] = None,
    since: Annotated[str | None, Query(max_length=64)] = None,
) -> StreamingResponse:
    """Server-Sent Events for one shop, resumable with the standard `Last-Event-ID` header."""
    resume_from = last_event_id or since
    if resume_from is not None and not events.is_valid_id(resume_from):
        resume_from = None
    shop_id = ctx.shop.id
    is_demo = ctx.shop.mode == ShopMode.DEMO
    # Don't hold a database connection for the lifetime of the stream.
    await db.close()

    async def generate() -> AsyncIterator[str]:
        pubsub = redis.pubsub()
        await pubsub.subscribe(events.channel(shop_id))
        try:
            yield "retry: 3000\n\n"
            sent = events.parse_id(resume_from) if resume_from else (0, 0)
            if resume_from:
                replay, complete = await events.replay_after(redis, shop_id, resume_from)
                if not complete:
                    yield "event: reset\ndata: {}\n\n"
                for event in replay:
                    sent = events.parse_id(event.id)
                    yield _sse(event)
            else:
                sent = events.parse_id(await events.last_id(redis, shop_id))
            if is_demo:
                await simulator.mark_watched(redis, shop_id)

            loop = asyncio.get_running_loop()
            last_beat = loop.time()
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is not None and message.get("type") == "message":
                    event = events.LiveEvent.from_json(message["data"])
                    # Pub/sub may redeliver what replay already sent; ids are monotonic.
                    if events.parse_id(event.id) > sent:
                        sent = events.parse_id(event.id)
                        yield _sse(event)
                if loop.time() - last_beat >= settings.sse_heartbeat_seconds:
                    last_beat = loop.time()
                    if await request.is_disconnected():
                        break
                    if is_demo:
                        await simulator.mark_watched(redis, shop_id)
                    yield f": keepalive {utcnow().isoformat()}\n\n"
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await pubsub.aclose()  # type: ignore[no-untyped-call]

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class SimulatorUpdate(BaseModel):
    running: bool


def _require_demo(ctx: ShopAdmin) -> None:
    if ctx.shop.mode != ShopMode.DEMO:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only available for demo stores")


@router.post("/demo/simulator")
async def update_simulator(
    body: SimulatorUpdate, ctx: ShopAdmin, db: DbSession, redis: RedisDep
) -> dict[str, bool]:
    _require_demo(ctx)
    await simulator.set_paused(redis, ctx.shop.id, not body.running)
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="demo.simulator_resumed" if body.running else "demo.simulator_paused",
    )
    await db.commit()
    await events.publish(redis, ctx.shop.id, "simulator.status", {"running": body.running})
    return {"running": body.running}


@router.post("/demo/scenarios/{name}", status_code=status.HTTP_202_ACCEPTED)
async def trigger_scenario(
    name: str, ctx: ShopAdmin, db: DbSession, redis: RedisDep
) -> dict[str, str]:
    _require_demo(ctx)
    if name not in simulator.SCENARIOS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown scenario")
    # One run of each scenario at a time per shop.
    lock = f"demo:{ctx.shop.id}:scenario:{name}"
    if not await redis.set(lock, "1", nx=True, ex=int(timedelta(seconds=45).total_seconds())):
        raise HTTPException(status.HTTP_409_CONFLICT, "Scenario is already running")
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="demo.scenario_triggered",
        details={"scenario": name},
    )
    await db.commit()
    await simulator.mark_watched(redis, ctx.shop.id)
    await get_queue().enqueue("run_demo_scenario", str(ctx.shop.id), name)
    return {"scenario": name, "status": "started"}
