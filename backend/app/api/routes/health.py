import asyncio

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import DbSession, RedisDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up and serving requests."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: DbSession, redis: RedisDep, response: Response) -> dict[str, str]:
    """Readiness: dependencies are reachable."""
    checks: dict[str, str] = {}
    try:
        async with asyncio.timeout(2):
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    try:
        async with asyncio.timeout(2):
            await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", **checks}
