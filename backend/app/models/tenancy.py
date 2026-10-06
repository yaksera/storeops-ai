import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, ShopScoped, Timestamps, UUIDPrimaryKey, str_enum
from app.models.enums import AgentName, Autonomy, Plan, Role, ShopMode, ShopStatus


class Shop(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "shops"

    name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    mode: Mapped[ShopMode] = mapped_column(str_enum(ShopMode, "shop_mode"), default=ShopMode.DEMO)
    status: Mapped[ShopStatus] = mapped_column(
        str_enum(ShopStatus, "shop_status"), default=ShopStatus.ACTIVE
    )
    plan: Mapped[Plan] = mapped_column(str_enum(Plan, "plan"), default=Plan.FREE)
    access_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    scopes: Mapped[str | None] = mapped_column(String(1024))
    installed_at: Mapped[datetime | None]
    uninstalled_at: Mapped[datetime | None]
    data_deletion_due_at: Mapped[datetime | None]

    settings: Mapped["ShopSettings"] = relationship(
        back_populates="shop", uselist=False, cascade="all, delete-orphan"
    )


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None]


class Membership(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("shop_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(str_enum(Role, "role"), default=Role.VIEWER)

    shop: Mapped[Shop] = relationship()
    user: Mapped[User] = relationship()


class ShopSettings(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "shop_settings"
    __table_args__ = (UniqueConstraint("shop_id"),)

    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours_start: Mapped[time | None]
    quiet_hours_end: Mapped[time | None]
    fraud_threshold: Mapped[int] = mapped_column(Integer, default=70)
    max_discount_pct: Mapped[int] = mapped_column(Integer, default=10)
    refund_ceiling_minor: Mapped[int] = mapped_column(Integer, default=10_000)
    alert_email: Mapped[str | None] = mapped_column(String(320))
    support_email: Mapped[str | None] = mapped_column(String(320))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    physical_address: Mapped[str | None] = mapped_column(String(512))
    alert_channels: Mapped[dict[str, Any]] = mapped_column(default=dict)

    shop: Mapped[Shop] = relationship(back_populates="settings")


class AgentConfig(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "agent_configs"
    __table_args__ = (UniqueConstraint("shop_id", "agent"),)

    agent: Mapped[AgentName] = mapped_column(str_enum(AgentName, "agent_name"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    autonomy: Mapped[Autonomy] = mapped_column(
        str_enum(Autonomy, "autonomy"), default=Autonomy.SUGGEST
    )
    daily_action_cap: Mapped[int] = mapped_column(Integer, default=50)
    model: Mapped[str | None] = mapped_column(String(128))
    tone: Mapped[str] = mapped_column(String(64), default="friendly")
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
