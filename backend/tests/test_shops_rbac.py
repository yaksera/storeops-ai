import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentConfig, AuditLog
from tests.conftest import Session, signup


async def test_create_demo_shop_provisions_defaults(
    owner: Session, demo_shop: dict[str, str], db: AsyncSession
) -> None:
    assert demo_shop["mode"] == "demo"
    assert demo_shop["role"] == "owner"
    assert demo_shop["domain"].endswith(".example")
    shop_id = uuid.UUID(demo_shop["id"])

    configs = (await db.scalars(select(AgentConfig).where(AgentConfig.shop_id == shop_id))).all()
    assert len(configs) == 8
    pricing = next(c for c in configs if c.agent == "pricing_advisor")
    assert pricing.autonomy == "suggest"

    me = (await owner.get("/api/auth/me")).json()
    assert [s["id"] for s in me["shops"]] == [demo_shop["id"]]

    settings = (await owner.get(f"/api/shops/{shop_id}/settings")).json()
    assert settings["fraud_threshold"] == 70
    assert settings["max_discount_pct"] == 10


async def test_non_member_gets_404(app: FastAPI, demo_shop: dict[str, str]) -> None:
    stranger = await signup(app, "stranger@elsewhere.example")
    response = await stranger.get(f"/api/shops/{demo_shop['id']}")
    assert response.status_code == 404
    response = await stranger.patch(
        f"/api/shops/{demo_shop['id']}/settings", json={"kill_switch": True}
    )
    assert response.status_code == 404
    await stranger.client.aclose()


async def test_role_hierarchy(
    app: FastAPI, owner: Session, demo_shop: dict[str, str], db: AsyncSession
) -> None:
    shop = demo_shop["id"]
    viewer = await signup(app, "viewer@northbound.example")
    admin = await signup(app, "admin@northbound.example")

    added = await owner.post(
        f"/api/shops/{shop}/members", json={"email": "viewer@northbound.example"}
    )
    assert added.status_code == 201
    assert added.json()["role"] == "viewer"
    added = await owner.post(
        f"/api/shops/{shop}/members", json={"email": "admin@northbound.example", "role": "admin"}
    )
    assert added.status_code == 201
    duplicate = await owner.post(
        f"/api/shops/{shop}/members", json={"email": "admin@northbound.example"}
    )
    assert duplicate.status_code == 409

    assert (await viewer.get(f"/api/shops/{shop}/settings")).status_code == 200
    assert (
        await viewer.patch(f"/api/shops/{shop}/settings", json={"dry_run": True})
    ).status_code == 403

    updated = await admin.patch(
        f"/api/shops/{shop}/settings", json={"dry_run": True, "fraud_threshold": 80}
    )
    assert updated.status_code == 200
    assert updated.json()["dry_run"] is True
    assert updated.json()["fraud_threshold"] == 80

    assert (
        await admin.post(f"/api/shops/{shop}/members", json={"email": "x@northbound.example"})
    ).status_code == 403

    members = (await viewer.get(f"/api/shops/{shop}/members")).json()
    assert {m["role"] for m in members} == {"owner", "admin", "viewer"}

    promoted = await owner.patch(
        f"/api/shops/{shop}/members/{viewer.user_id}", json={"role": "admin"}
    )
    assert promoted.json()["role"] == "admin"
    assert (await owner.delete(f"/api/shops/{shop}/members/{admin.user_id}")).status_code == 204
    assert (await admin.get(f"/api/shops/{shop}")).status_code == 404

    actions = (
        await db.scalars(
            select(AuditLog.action).where(AuditLog.shop_id == uuid.UUID(shop)).order_by(AuditLog.id)
        )
    ).all()
    assert actions == [
        "shop.created",
        "member.added",
        "member.added",
        "settings.updated",
        "member.role_changed",
        "member.removed",
    ]
    for session in (viewer, admin):
        await session.client.aclose()


async def test_last_owner_cannot_be_demoted(owner: Session, demo_shop: dict[str, str]) -> None:
    response = await owner.patch(
        f"/api/shops/{demo_shop['id']}/members/{owner.user_id}", json={"role": "viewer"}
    )
    assert response.status_code == 409
    response = await owner.delete(f"/api/shops/{demo_shop['id']}/members/{owner.user_id}")
    assert response.status_code == 409


async def test_settings_validation(owner: Session, demo_shop: dict[str, str]) -> None:
    url = f"/api/shops/{demo_shop['id']}/settings"
    assert (await owner.patch(url, json={"max_discount_pct": 25})).status_code == 422
    assert (await owner.patch(url, json={"fraud_threshold": 101})).status_code == 422
    assert (await owner.patch(url, json={"unknown_field": 1})).status_code == 422


async def test_audit_log_is_append_only(demo_shop: dict[str, str], db: AsyncSession) -> None:
    shop_id = uuid.UUID(demo_shop["id"])
    count = await db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.shop_id == shop_id)
    )
    assert count == 1

    for statement in ("UPDATE audit_log SET action = 'tampered'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text(statement))
        await db.rollback()

    await db.execute(text("SET LOCAL storeops.audit_purge = 'on'"))
    await db.execute(text("DELETE FROM audit_log"))
    await db.commit()
