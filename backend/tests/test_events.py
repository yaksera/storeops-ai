import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator

import fakeredis
import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.core.config import get_settings
from app.services import events
from tests.conftest import Session

SHOP = "00000000-0000-0000-0000-000000000001"


async def test_publish_replay_and_recent(redis: fakeredis.FakeAsyncRedis) -> None:
    first = await events.publish(redis, SHOP, "activity", {"n": 1})
    await events.publish(redis, SHOP, "order.created", {"n": 2})
    await events.publish(redis, SHOP, "activity", {"n": 3})

    replay, complete = await events.replay_after(redis, SHOP, first.id)
    assert complete
    assert [e.data["n"] for e in replay] == [2, 3]
    assert [e.data["n"] for e in await events.recent(redis, SHOP, 10, {"activity"})] == [3, 1]
    assert await events.last_id(redis, SHOP) == replay[-1].id
    assert events.parse_id(replay[0].id) > events.parse_id(first.id)


async def test_replay_reports_gap_when_stream_was_trimmed(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "event_stream_maxlen", 2)
    for n in range(5):
        await events.publish(redis, SHOP, "activity", {"n": n})
    await redis.xtrim(events.stream_key(SHOP), maxlen=2, approximate=False)
    replay, complete = await events.replay_after(redis, SHOP, "1-0")
    assert not complete
    assert [e.data["n"] for e in replay] == [3, 4]


def test_id_validation() -> None:
    assert events.is_valid_id("1700000000000-3")
    assert not events.is_valid_id("abc")


async def test_poll_endpoint(
    owner: Session, demo_shop: dict[str, str], redis: fakeredis.FakeAsyncRedis
) -> None:
    first = await events.publish(redis, demo_shop["id"], "activity", {"n": 1})
    await events.publish(redis, demo_shop["id"], "activity", {"n": 2})
    response = await owner.get(f"/api/shops/{demo_shop['id']}/events?after={first.id}")
    assert response.status_code == 200
    body = response.json()
    assert [e["data"]["n"] for e in body["events"]] == [2]
    assert body["reset"] is False
    assert (await owner.get(f"/api/shops/{demo_shop['id']}/events?after=nope")).status_code == 422


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.asynccontextmanager
async def _serve(app: FastAPI) -> AsyncIterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes no startup event
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def _read_events(lines: AsyncIterator[str], count: int) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    current: dict[str, str] = {}
    async with asyncio.timeout(5):
        async for line in lines:
            if line == "":
                if "event" in current:
                    parsed.append(current)
                    if len(parsed) == count:
                        return parsed
                current = {}
                continue
            key, _, value = line.partition(": ")
            if key in {"id", "event", "data"}:
                current[key] = value
    return parsed


async def test_sse_stream_resumes_from_last_event_id_and_streams_live(
    app: FastAPI,
    owner: Session,
    demo_shop: dict[str, str],
    redis: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "sse_heartbeat_seconds", 0.2)
    shop_id = demo_shop["id"]
    seen = await events.publish(redis, shop_id, "activity", {"n": 1})
    await events.publish(redis, shop_id, "activity", {"n": 2})

    async with _serve(app) as base_url, httpx.AsyncClient(base_url=base_url) as client:
        token = owner.client.cookies.get("storeops_session")
        unauthenticated = await httpx.AsyncClient(base_url=base_url).get(
            f"/api/shops/{shop_id}/events/stream"
        )
        assert unauthenticated.status_code == 401

        async with client.stream(
            "GET",
            f"/api/shops/{shop_id}/events/stream",
            headers={"Last-Event-ID": seen.id, "Cookie": f"storeops_session={token}"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.aiter_lines()
            replayed = await _read_events(lines, 1)
            assert replayed[0]["event"] == "activity"
            assert '"n": 2' in replayed[0]["data"]

            live = await events.publish(redis, shop_id, "order.created", {"n": 3})
            received = await _read_events(lines, 1)
            assert received[0]["id"] == live.id
            assert received[0]["event"] == "order.created"
