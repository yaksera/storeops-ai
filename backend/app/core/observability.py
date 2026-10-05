"""Error tracking (Sentry) and tracing (OpenTelemetry). Both are opt-in through environment
variables and no-ops otherwise."""

import logging
from typing import Any

from fastapi import FastAPI

from app.core.config import Settings

logger = logging.getLogger("storeops.observability")

_SENSITIVE_HEADERS = {"cookie", "authorization", "x-csrf-token", "x-shopify-access-token"}


def _scrub(event: Any, hint: Any) -> Any:
    """Never ship cookies, tokens or customer emails to the error tracker."""
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    for key in list(headers):
        if key.lower() in _SENSITIVE_HEADERS:
            headers[key] = "[filtered]"
    request.pop("cookies", None)
    request.pop("data", None)
    return event


def init_sentry(settings: Settings, component: str) -> bool:
    if not settings.sentry_dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.release,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        before_send=_scrub,
    )
    sentry_sdk.set_tag("component", component)
    return True


def init_tracing(settings: Settings, component: str, app: FastAPI | None = None) -> bool:
    if not settings.otel_exporter_otlp_endpoint:
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from app.core.db import get_engine

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": f"storeops-{component}",
                "deployment.environment": settings.environment,
                "service.version": settings.release or "dev",
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)
    SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready")
    logger.info("tracing enabled", extra={"component": component})
    return True
