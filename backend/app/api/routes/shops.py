import secrets
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import Auth, DbSession, ShopAdmin, ShopOwner, ShopViewer
from app.api.schemas import (
    AddMemberRequest,
    CreateDemoShopRequest,
    MemberOut,
    ShopSettingsOut,
    ShopSettingsUpdate,
    ShopSummary,
    UpdateMemberRequest,
)
from app.demo.seed import seed_demo_shop
from app.models import Membership, ShopSettings, User
from app.models.enums import ActorType, Role, ShopMode
from app.services import audit
from app.services.shops import create_shop

router = APIRouter(prefix="/api/shops", tags=["shops"])


@router.post("/demo", status_code=status.HTTP_201_CREATED)
async def create_demo_shop(body: CreateDemoShopRequest, auth: Auth, db: DbSession) -> ShopSummary:
    domain = f"northbound-{secrets.token_hex(3)}.example"
    shop = await create_shop(
        db,
        owner=auth.user,
        name=body.name,
        domain=domain,
        mode=ShopMode.DEMO,
        timezone="America/Denver",
    )
    await seed_demo_shop(db, shop)
    await db.commit()
    return ShopSummary(
        id=shop.id,
        name=shop.name,
        domain=shop.domain,
        mode=shop.mode,
        plan=shop.plan,
        currency=shop.currency,
        timezone=shop.timezone,
        role=Role.OWNER,
    )


@router.get("/{shop_id}")
async def get_shop(ctx: ShopViewer) -> ShopSummary:
    shop = ctx.shop
    return ShopSummary(
        id=shop.id,
        name=shop.name,
        domain=shop.domain,
        mode=shop.mode,
        plan=shop.plan,
        currency=shop.currency,
        timezone=shop.timezone,
        role=ctx.role,
    )


async def _settings(db: DbSession, shop_id: uuid.UUID) -> ShopSettings:
    row = (
        await db.execute(select(ShopSettings).where(ShopSettings.shop_id == shop_id))
    ).scalar_one()
    return row


@router.get("/{shop_id}/settings")
async def get_settings_(ctx: ShopViewer, db: DbSession) -> ShopSettingsOut:
    return ShopSettingsOut.model_validate(await _settings(db, ctx.shop.id))


@router.patch("/{shop_id}/settings")
async def update_settings(
    body: ShopSettingsUpdate, ctx: ShopAdmin, db: DbSession
) -> ShopSettingsOut:
    row = await _settings(db, ctx.shop.id)
    changes = body.model_dump(exclude_unset=True)
    before = {key: getattr(row, key) for key in changes}
    for key, value in changes.items():
        setattr(row, key, value)
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="settings.updated",
        target_type="shop_settings",
        target_id=row.id,
        details={
            "changes": {
                key: {"from": str(before[key]), "to": str(value)} for key, value in changes.items()
            }
        },
    )
    await db.commit()
    return ShopSettingsOut.model_validate(row)


@router.get("/{shop_id}/members")
async def list_members(ctx: ShopViewer, db: DbSession) -> list[MemberOut]:
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.shop_id == ctx.shop.id)
            .order_by(Membership.created_at)
        )
    ).all()
    return [
        MemberOut(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            joined_at=membership.created_at,
        )
        for membership, user in rows
    ]


@router.post("/{shop_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(body: AddMemberRequest, ctx: ShopOwner, db: DbSession) -> MemberOut:
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No account exists for that email")
    existing = await db.scalar(
        select(Membership).where(Membership.shop_id == ctx.shop.id, Membership.user_id == user.id)
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member")
    membership = Membership(shop_id=ctx.shop.id, user_id=user.id, role=body.role)
    db.add(membership)
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="member.added",
        target_type="user",
        target_id=user.id,
        details={"role": body.role.value},
    )
    await db.commit()
    return MemberOut(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        joined_at=membership.created_at,
    )


async def _member(db: DbSession, shop_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    membership = await db.scalar(
        select(Membership).where(Membership.shop_id == shop_id, Membership.user_id == user_id)
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return membership


async def _ensure_other_owner(db: DbSession, shop_id: uuid.UUID, user_id: uuid.UUID) -> None:
    owners = await db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.shop_id == shop_id,
            Membership.role == Role.OWNER,
            Membership.user_id != user_id,
        )
    )
    if not owners:
        raise HTTPException(status.HTTP_409_CONFLICT, "A shop must keep at least one owner")


@router.patch("/{shop_id}/members/{user_id}")
async def update_member(
    user_id: uuid.UUID, body: UpdateMemberRequest, ctx: ShopOwner, db: DbSession
) -> MemberOut:
    membership = await _member(db, ctx.shop.id, user_id)
    if membership.role == Role.OWNER and body.role != Role.OWNER:
        await _ensure_other_owner(db, ctx.shop.id, user_id)
    previous = membership.role
    membership.role = body.role
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="member.role_changed",
        target_type="user",
        target_id=user_id,
        details={"from": previous.value, "to": body.role.value},
    )
    await db.commit()
    user = await db.get(User, user_id)
    assert user is not None
    return MemberOut(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        joined_at=membership.created_at,
    )


@router.delete("/{shop_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, ctx: ShopOwner, db: DbSession) -> None:
    membership = await _member(db, ctx.shop.id, user_id)
    if membership.role == Role.OWNER:
        await _ensure_other_owner(db, ctx.shop.id, user_id)
    await db.delete(membership)
    audit.record(
        db,
        shop_id=ctx.shop.id,
        actor_type=ActorType.USER,
        actor_id=ctx.user.id,
        action="member.removed",
        target_type="user",
        target_id=user_id,
    )
    await db.commit()
