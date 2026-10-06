"""Single OpenRouter client used by every agent.

JSON-mode completions validated against a Pydantic model, with timeouts, bounded retries and a
`llm_calls` row (tokens, cost, latency) for every attempt. Callers must treat `None` as "use your
deterministic fallback": the client never raises for provider errors.
"""

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import LlmCall
from app.models.enums import AgentName, LlmCallStatus

logger = logging.getLogger("storeops.llm")

# USD per million tokens (input, output). Unknown models are logged at zero cost.
PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "google/gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "google/gemini-2.5-flash-lite": (Decimal("0.10"), Decimal("0.40")),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = PRICING.get(model, (Decimal(0), Decimal(0)))
    total = (price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)
    return total.quantize(Decimal("0.000001"))


@dataclass(frozen=True, slots=True)
class LlmResult[T: BaseModel]:
    value: T
    model: str
    cost_usd: Decimal
    latency_ms: int


class LlmClient:
    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        shop_id: uuid.UUID,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        assert settings.openrouter_api_key is not None
        self._settings = settings
        self._db = db
        self._shop_id = shop_id
        self._http = httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            timeout=settings.llm_timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}",
                "X-Title": "StoreOps AI",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete_json[T: BaseModel](
        self,
        *,
        agent: AgentName,
        system: str,
        user: str,
        schema: type[T],
        prompt_version: str,
        model: str | None = None,
        agent_run_id: uuid.UUID | None = None,
    ) -> LlmResult[T] | None:
        model = model or self._settings.llm_default_model
        body: dict[str, Any] = {
            "model": model,
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
            "messages": [
                {
                    "role": "system",
                    "content": f"{system}\n\nRespond with a single JSON object matching this "
                    f"JSON schema:\n{json.dumps(schema.model_json_schema())}",
                },
                {"role": "user", "content": user},
            ],
        }
        for attempt in range(self._settings.llm_max_retries + 1):
            started = time.perf_counter()
            status, error = LlmCallStatus.ERROR, None
            usage: dict[str, Any] = {}
            try:
                response = await self._http.post("/chat/completions", json=body)
                response.raise_for_status()
                payload = response.json()
                usage = payload.get("usage") or {}
                content = payload["choices"][0]["message"]["content"]
                value = schema.model_validate_json(content)
                status = LlmCallStatus.OK
            except httpx.TimeoutException as exc:
                status, error = LlmCallStatus.TIMEOUT, str(exc) or "timeout"
            except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError) as exc:
                error = f"{type(exc).__name__}: {exc}"[:2000]
            latency = int((time.perf_counter() - started) * 1000)
            in_tok = int(usage.get("prompt_tokens") or 0)
            out_tok = int(usage.get("completion_tokens") or 0)
            cost = cost_usd(model, in_tok, out_tok)
            self._db.add(
                LlmCall(
                    shop_id=self._shop_id,
                    agent_run_id=agent_run_id,
                    agent=agent,
                    provider="openrouter",
                    model=model,
                    prompt_version=prompt_version,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost,
                    latency_ms=latency,
                    status=status,
                    error=error,
                )
            )
            await self._db.flush()
            if status == LlmCallStatus.OK:
                return LlmResult(value=value, model=model, cost_usd=cost, latency_ms=latency)
            logger.warning(
                "llm call failed", extra={"agent": agent.value, "attempt": attempt, "error": error}
            )
            if attempt < self._settings.llm_max_retries:
                await asyncio.sleep(min(4.0, 0.5 * 2**attempt) + random.uniform(0, 0.25))  # noqa: S311
        return None
