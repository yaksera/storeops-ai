import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ShopScoped, Timestamps, UUIDPrimaryKey, str_enum
from app.models.enums import (
    BillingProvider,
    NotificationChannel,
    Plan,
    Severity,
    SubscriptionStatus,
)


class Notification(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_shop_created", "shop_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[NotificationChannel] = mapped_column(
        str_enum(NotificationChannel, "notification_channel"), default=NotificationChannel.IN_APP
    )
    kind: Mapped[str] = mapped_column(String(64))
    severity: Mapped[Severity] = mapped_column(
        str_enum(Severity, "severity"), default=Severity.INFO
    )
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    read_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]


class UsageCounter(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    """Monthly usage per metric, e.g. `ai_actions` or `llm_cost_micro_usd`."""

    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("shop_id", "period", "metric"),)

    period: Mapped[date]
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[int] = mapped_column(BigInteger, default=0)


class Subscription(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "subscriptions"

    plan: Mapped[Plan] = mapped_column(str_enum(Plan, "plan"))
    status: Mapped[SubscriptionStatus] = mapped_column(
        str_enum(SubscriptionStatus, "subscription_status"), default=SubscriptionStatus.PENDING
    )
    provider: Mapped[BillingProvider] = mapped_column(
        str_enum(BillingProvider, "billing_provider"), default=BillingProvider.NONE
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    trial_ends_at: Mapped[datetime | None]
    current_period_end: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]
