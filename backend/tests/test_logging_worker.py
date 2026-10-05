import json
import logging

from app.core.logging import JsonFormatter, request_id_var
from app.worker.tasks import ping


def test_json_formatter_includes_extra_fields_and_request_id() -> None:
    record = logging.LogRecord(
        "storeops.test", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    record.shop_id = "shop-1"
    token = request_id_var.set("req-42")
    try:
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)
    payload = json.loads(line)
    assert payload["msg"] == "hello world"
    assert payload["level"] == "info"
    assert payload["shop_id"] == "shop-1"
    assert payload["request_id"] == "req-42"


async def test_worker_ping_reaches_database() -> None:
    assert await ping({}) == "pong"
