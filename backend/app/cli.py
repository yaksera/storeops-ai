"""Operational commands.

uv run python -m app.cli seed            # demo user + seeded Northbound store
uv run python -m app.cli bench -n 1000   # pipeline throughput (events/s, latency)
"""

import argparse
import asyncio
import random
import statistics
import sys
import time
import uuid
from typing import Any

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.core.db import dispose_engine, get_sessionmaker
from app.core.redis import close_redis, get_redis
from app.demo.seed import seed_demo_shop
from app.demo.simulator import DemoStore, Line
from app.models import User
from app.models.enums import ShopMode
from app.pipeline.processor import process_event
from app.services.shops import create_shop

DEMO_EMAIL = "demo@northbound.example"
DEMO_PASSWORD = "northbound-demo"  # noqa: S105 - documented demo credentials for local use


async def seed(email: str, password: str) -> None:
    async with get_sessionmaker()() as db:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash=hash_password(password), full_name="Demo Owner")
            db.add(user)
            await db.flush()
        shop = await create_shop(
            db,
            owner=user,
            name="Northbound Outdoor Gear",
            domain=f"northbound-{uuid.uuid4().hex[:6]}.example",
            mode=ShopMode.DEMO,
            timezone="America/Denver",
        )
        await seed_demo_shop(db, shop)
        await db.commit()
        print(f"Seeded {shop.name} ({shop.id})")
        print(f"Sign in at http://localhost:3000/login with {email} / {password}")


class _CollectingQueue:
    def __init__(self) -> None:
        self.event_ids: list[str] = []

    async def enqueue(
        self, function: str, *args: Any, job_id: str | None = None, priority: bool = False
    ) -> None:
        if function == "process_webhook_event":
            self.event_ids.append(str(args[0]))


async def bench(events: int, concurrency: int) -> None:
    """Ingest `events` simulated orders, then process them with `concurrency` parallel jobs
    (Arq runs up to 50 per worker) and report throughput and per-event latency."""
    sessionmaker = get_sessionmaker()
    redis = get_redis()
    queue = _CollectingQueue()
    async with sessionmaker() as db:
        owner = User(
            email=f"bench-{uuid.uuid4().hex[:8]}@bench.example",
            password_hash=hash_password(uuid.uuid4().hex),
        )
        db.add(owner)
        await db.flush()
        shop = await create_shop(
            db,
            owner=owner,
            name="Bench",
            domain=f"bench-{uuid.uuid4().hex[:6]}.example",
            mode=ShopMode.DEMO,
        )
        await seed_demo_shop(db, shop)
        await db.commit()
        store = await DemoStore(shop, db, redis, queue, rng=random.Random(1)).load()  # noqa: S311
        for item in store.stock:
            item.quantity = 1_000_000
        started = time.perf_counter()
        while len(queue.event_ids) < events:
            item = store.rng.choice(store.stock)
            await store.place_order(await store.random_customer(), [Line(item, 1)])
        ingest_seconds = time.perf_counter() - started

    latencies: list[float] = []
    semaphore = asyncio.Semaphore(concurrency)

    async def run(event_id: str) -> None:
        async with semaphore:
            t0 = time.perf_counter()
            await process_event(sessionmaker, redis, uuid.UUID(event_id))
            latencies.append(time.perf_counter() - t0)

    started = time.perf_counter()
    await asyncio.gather(*(run(e) for e in queue.event_ids[:events]))
    elapsed = time.perf_counter() - started
    latencies.sort()
    print(f"events:           {len(latencies)}")
    print(f"ingest (ack):     {ingest_seconds / len(queue.event_ids) * 1000:.1f} ms/event")
    print(f"processing:       {len(latencies) / elapsed:.1f} events/s at concurrency {concurrency}")
    print(
        f"latency p50/p95:  {statistics.median(latencies) * 1000:.0f} / "
        f"{latencies[int(len(latencies) * 0.95) - 1] * 1000:.0f} ms"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    seed_cmd = sub.add_parser("seed", help="create the demo user and a seeded demo store")
    seed_cmd.add_argument("--email", default=DEMO_EMAIL)
    seed_cmd.add_argument("--password", default=DEMO_PASSWORD)
    bench_cmd = sub.add_parser("bench", help="measure pipeline throughput")
    bench_cmd.add_argument("-n", "--events", type=int, default=500)
    bench_cmd.add_argument("-c", "--concurrency", type=int, default=20)
    args = parser.parse_args(argv)

    async def run() -> None:
        try:
            if args.command == "seed":
                await seed(args.email, args.password)
            else:
                await bench(args.events, args.concurrency)
        finally:
            await close_redis()
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main(sys.argv[1:])
