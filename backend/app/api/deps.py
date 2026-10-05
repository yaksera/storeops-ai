import hmac
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import SessionData, load_session
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.redis import get_redis
from app.models import Membership, Shop, User
from app.models.enums import Role, ShopStatus

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

DbSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def redis_dep() -> Redis:
    return get_redis()


RedisDep = Annotated[Redis, Depends(redis_dep)]


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: SessionData
    token: str


async def get_auth(
    request: Request,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> AuthContext:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    session = await load_session(redis, token)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    if request.method not in SAFE_METHODS and not (
        x_csrf_token and hmac.compare_digest(x_csrf_token, session.csrf_token)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return AuthContext(user=user, session=session, token=token)


Auth = Annotated[AuthContext, Depends(get_auth)]


@dataclass(frozen=True, slots=True)
class ShopContext:
    shop: Shop
    user: User
    role: Role


def require_shop_role(
    minimum: Role,
) -> Callable[..., Coroutine[Any, Any, ShopContext]]:
    """Dependency factory: resolves `shop_id` from the path and enforces RBAC.

    Non-members get 404 rather than 403 so shop ids cannot be probed.
    """

    async def dependency(shop_id: uuid.UUID, auth: Auth, db: DbSession) -> ShopContext:
        row = (
            await db.execute(
                select(Shop, Membership.role)
                .join(Membership, Membership.shop_id == Shop.id)
                .where(Shop.id == shop_id, Membership.user_id == auth.user.id)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")
        shop, role = row
        if shop.status != ShopStatus.ACTIVE:
            raise HTTPException(status.HTTP_410_GONE, "Shop has been uninstalled")
        if role.rank < minimum.rank:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {minimum.value} role")
        return ShopContext(shop=shop, user=auth.user, role=role)

    return dependency


ShopViewer = Annotated[ShopContext, Depends(require_shop_role(Role.VIEWER))]
ShopAdmin = Annotated[ShopContext, Depends(require_shop_role(Role.ADMIN))]
ShopOwner = Annotated[ShopContext, Depends(require_shop_role(Role.OWNER))]
