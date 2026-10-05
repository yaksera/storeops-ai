import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://storeops:storeops@localhost:5432/storeops_test"
)
os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "1000"

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import fakeredis
import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine, get_sessionmaker
from app.core.queue import set_queue
from app.core.redis import get_redis, set_redis
from app.main import create_app
from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _run_migrations() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
async def _database() -> AsyncIterator[None]:
    import asyncio

    await asyncio.to_thread(_run_migrations)
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
async def _clean_tables(_database: None) -> AsyncIterator[None]:
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    set_redis(client)
    yield client
    await client.flushall()
    set_redis(None)


class InlineQueue:
    """Runs jobs immediately in-process, like a worker with zero latency."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...], str | None]] = []

    async def enqueue(self, function: str, *args: object, job_id: str | None = None) -> None:
        import uuid

        from app.demo import simulator
        from app.pipeline.processor import process_event

        self.jobs.append((function, args, job_id))
        if function == "process_webhook_event":
            await process_event(get_sessionmaker(), get_redis(), uuid.UUID(str(args[0])))
        elif function == "run_demo_scenario":
            await simulator.run_scenario(
                get_sessionmaker(), get_redis(), self, uuid.UUID(str(args[0])), str(args[1]), 0
            )


@pytest.fixture
def queue(redis: fakeredis.FakeAsyncRedis) -> Iterator[InlineQueue]:
    inline = InlineQueue()
    set_queue(inline)
    yield inline
    set_queue(None)


@pytest.fixture
def app(redis: fakeredis.FakeAsyncRedis, queue: InlineQueue) -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


class Session:
    """A signed-in browser: cookies live on the client, CSRF token is sent as a header."""

    def __init__(self, client: httpx.AsyncClient, csrf: str, user_id: str) -> None:
        self.client = client
        self.csrf = csrf
        self.user_id = user_id

    def _headers(self) -> dict[str, str]:
        return {"x-csrf-token": self.csrf}

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        return await self.client.get(url, **kwargs)  # type: ignore[arg-type]

    async def post(self, url: str, json: object = None) -> httpx.Response:
        return await self.client.post(url, json=json, headers=self._headers())

    async def patch(self, url: str, json: object = None) -> httpx.Response:
        return await self.client.patch(url, json=json, headers=self._headers())

    async def delete(self, url: str) -> httpx.Response:
        return await self.client.delete(url, headers=self._headers())


async def signup(app: FastAPI, email: str, password: str = "correct horse battery") -> Session:
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    response = await client.post(
        "/api/auth/signup", json={"email": email, "password": password, "full_name": "Test User"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return Session(client, body["csrf_token"], body["user"]["id"])


@pytest.fixture
async def owner(app: FastAPI) -> AsyncIterator[Session]:
    session = await signup(app, "owner@northbound.example")
    yield session
    await session.client.aclose()


@pytest.fixture
async def demo_shop(owner: Session) -> dict[str, str]:
    response = await owner.post("/api/shops/demo", json={})
    assert response.status_code == 201, response.text
    data: dict[str, str] = response.json()
    return data
