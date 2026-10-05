import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import SAFE_METHODS
from app.api.routes import (
    agents,
    audit_log,
    auth,
    billing,
    health,
    insights,
    live,
    operations,
    privacy,
    proposals,
    shopify,
    shops,
    support,
)
from app.core.config import Settings, get_settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging, request_id_var
from app.core.observability import init_sentry, init_tracing
from app.core.queue import close_queue
from app.core.redis import close_redis

logger = logging.getLogger("storeops.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("api starting", extra={"environment": settings.environment})
        yield
        await close_queue()
        await close_redis()
        await dispose_engine()

    app = FastAPI(
        title="StoreOps AI",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )

    allowed_origins = {settings.frontend_origin, *settings.cors_origins}

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id[:64])
        started = time.perf_counter()
        try:
            # Defence in depth on top of SameSite cookies + CSRF tokens: browsers always send
            # Origin on cross-site unsafe requests, so reject unknown origins outright.
            origin = request.headers.get("origin")
            if request.method not in SAFE_METHODS and origin and origin not in allowed_origins:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = request_id[:64]
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        response.headers["x-frame-options"] = "DENY"
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["content-type", "x-csrf-token", "last-event-id", "x-request-id"],
    )

    init_sentry(settings, "api")
    init_tracing(settings, "api", app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(shops.router)
    app.include_router(live.router)
    app.include_router(proposals.router)
    app.include_router(agents.router)
    app.include_router(audit_log.router)
    app.include_router(operations.router)
    app.include_router(shopify.router)
    app.include_router(support.router)
    app.include_router(insights.router)
    app.include_router(billing.router)
    app.include_router(privacy.router)
    return app


app = create_app()
