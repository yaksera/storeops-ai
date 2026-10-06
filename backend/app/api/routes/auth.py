from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import Auth, DbSession, RedisDep, SettingsDep
from app.api.schemas import LoginRequest, MeResponse, ShopSummary, SignupRequest, UserOut
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.sessions import create_session, destroy_session
from app.core import rate_limit
from app.core.config import Settings
from app.models import Membership, Shop, User
from app.models.base import utcnow
from app.models.enums import ShopStatus

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _enforce_rate_limit(
    redis: RedisDep, settings: Settings, request: Request, action: str
) -> None:
    allowed = await rate_limit.hit(
        redis, f"{action}:{_client_ip(request)}", settings.auth_rate_limit_per_minute
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many attempts, try again in a minute",
            headers={"Retry-After": "60"},
        )


def _set_auth_cookies(response: Response, settings: Settings, token: str, csrf: str) -> None:
    common = {
        "max_age": settings.session_ttl_seconds,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(settings.session_cookie_name, token, httponly=True, **common)  # type: ignore[arg-type]
    response.set_cookie(settings.csrf_cookie_name, csrf, httponly=False, **common)  # type: ignore[arg-type]


async def _me(db: DbSession, user: User, csrf: str) -> MeResponse:
    rows = (
        await db.execute(
            select(Shop, Membership.role)
            .join(Membership, Membership.shop_id == Shop.id)
            .where(Membership.user_id == user.id, Shop.status == ShopStatus.ACTIVE)
            .order_by(Shop.created_at)
        )
    ).all()
    shops = [
        ShopSummary(
            id=shop.id,
            name=shop.name,
            domain=shop.domain,
            mode=shop.mode,
            plan=shop.plan,
            currency=shop.currency,
            timezone=shop.timezone,
            role=role,
        )
        for shop, role in rows
    ]
    return MeResponse(user=UserOut.model_validate(user), shops=shops, csrf_token=csrf)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> MeResponse:
    await _enforce_rate_limit(redis, settings, request, "signup")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name.strip(),
        last_login_at=utcnow(),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists"
        ) from None
    token, csrf = await create_session(redis, user.id, settings.session_ttl_seconds)
    _set_auth_cookies(response, settings, token, csrf)
    return await _me(db, user, csrf)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> MeResponse:
    await _enforce_rate_limit(redis, settings, request, "login")
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utcnow()
    await db.commit()
    token, csrf = await create_session(redis, user.id, settings.session_ttl_seconds)
    _set_auth_cookies(response, settings, token, csrf)
    return await _me(db, user, csrf)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(auth: Auth, response: Response, redis: RedisDep, settings: SettingsDep) -> None:
    await destroy_session(redis, auth.token)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


@router.get("/me")
async def me(auth: Auth, db: DbSession) -> MeResponse:
    return await _me(db, auth.user, auth.session.csrf_token)
