import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, ShopifyId, ShopScoped, Timestamps, UUIDPrimaryKey, str_enum
from app.models.enums import (
    CheckoutStatus,
    EventSource,
    MessageAuthor,
    MessageDirection,
    ReviewSource,
    TicketChannel,
    TicketStatus,
    WebhookStatus,
)


class WebhookEvent(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    """Raw inbound event. `webhook_id` is the idempotency key for duplicate deliveries."""

    __tablename__ = "webhook_events"
    __table_args__ = (Index("ix_webhook_events_shop_status", "shop_id", "status"),)

    webhook_id: Mapped[str] = mapped_column(String(128), unique=True)
    topic: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[EventSource] = mapped_column(str_enum(EventSource, "event_source"))
    payload: Mapped[dict[str, Any]]
    status: Mapped[WebhookStatus] = mapped_column(
        str_enum(WebhookStatus, "webhook_status"), default=WebhookStatus.RECEIVED
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_at: Mapped[datetime | None]
    received_at: Mapped[datetime]
    processed_at: Mapped[datetime | None]


class Customer(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    """Minimal PII: only what agents need to act and to honour consent."""

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("shop_id", "shopify_id"),)

    shopify_id: Mapped[int] = mapped_column(ShopifyId)
    email: Mapped[str | None] = mapped_column(String(320))
    first_name: Mapped[str | None] = mapped_column(String(128))
    country_code: Mapped[str | None] = mapped_column(String(2))
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    total_spent_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    accepts_marketing: Mapped[bool] = mapped_column(Boolean, default=False)
    unsubscribed_at: Mapped[datetime | None]
    redacted_at: Mapped[datetime | None]


class Product(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("shop_id", "shopify_id"),)

    shopify_id: Mapped[int] = mapped_column(ShopifyId)
    title: Mapped[str] = mapped_column(String(255))
    handle: Mapped[str] = mapped_column(String(255))
    vendor: Mapped[str | None] = mapped_column(String(255))
    product_type: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active")
    image_url: Mapped[str | None] = mapped_column(String(1024))
    shopify_updated_at: Mapped[datetime | None]

    variants: Mapped[list["Variant"]] = relationship(back_populates="product")


class Variant(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "variants"
    __table_args__ = (UniqueConstraint("shop_id", "shopify_id"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    shopify_id: Mapped[int] = mapped_column(ShopifyId)
    inventory_item_id: Mapped[int | None] = mapped_column(ShopifyId, index=True)
    sku: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(255))
    price_minor: Mapped[int] = mapped_column(BigInteger)
    cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    inventory_quantity: Mapped[int] = mapped_column(Integer, default=0)
    reorder_point: Mapped[int | None] = mapped_column(Integer)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=14)

    product: Mapped[Product] = relationship(back_populates="variants")


class InventorySnapshot(UUIDPrimaryKey, ShopScoped, Base):
    __tablename__ = "inventory_snapshots"
    __table_args__ = (Index("ix_inventory_snapshots_variant_time", "variant_id", "recorded_at"),)

    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column(Integer)
    delta: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str | None] = mapped_column(String(64))
    recorded_at: Mapped[datetime]


class Checkout(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "checkouts"
    __table_args__ = (
        UniqueConstraint("shop_id", "token"),
        Index("ix_checkouts_shop_status", "shop_id", "status"),
        CheckConstraint("reminders_sent >= 0 AND reminders_sent <= 2", name="reminders_range"),
    )

    shopify_id: Mapped[int | None] = mapped_column(ShopifyId)
    token: Mapped[str] = mapped_column(String(128))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    email: Mapped[str | None] = mapped_column(String(320))
    currency: Mapped[str] = mapped_column(String(3))
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    line_items: Mapped[list[Any]] = mapped_column(default=list)
    recovery_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[CheckoutStatus] = mapped_column(
        str_enum(CheckoutStatus, "checkout_status"), default=CheckoutStatus.OPEN
    )
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    recovered_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL")
    )
    shopify_created_at: Mapped[datetime | None]
    shopify_updated_at: Mapped[datetime | None]


class Order(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "shopify_id"),
        Index("ix_orders_shop_processed", "shop_id", "processed_at"),
    )

    shopify_id: Mapped[int] = mapped_column(ShopifyId)
    name: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    checkout_token: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(320))
    currency: Mapped[str] = mapped_column(String(3))
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    discount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    financial_status: Mapped[str | None] = mapped_column(String(32))
    fulfillment_status: Mapped[str | None] = mapped_column(String(32))
    shipping_country: Mapped[str | None] = mapped_column(String(2))
    billing_country: Mapped[str | None] = mapped_column(String(2))
    source_name: Mapped[str | None] = mapped_column(String(64))
    risk_score: Mapped[int | None] = mapped_column(Integer)
    tracking_number: Mapped[str | None] = mapped_column(String(128))
    tracking_url: Mapped[str | None] = mapped_column(String(1024))
    is_held: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None]
    shopify_updated_at: Mapped[datetime | None]

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(UUIDPrimaryKey, ShopScoped, Base):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("variants.id", ondelete="SET NULL"), index=True
    )
    shopify_line_item_id: Mapped[int | None] = mapped_column(ShopifyId)
    title: Mapped[str] = mapped_column(String(255))
    sku: Mapped[str | None] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(Integer)
    price_minor: Mapped[int] = mapped_column(BigInteger)

    order: Mapped[Order] = relationship(back_populates="items")


class Review(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("shop_id", "source", "external_id"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
    )

    source: Mapped[ReviewSource] = mapped_column(str_enum(ReviewSource, "review_source"))
    external_id: Mapped[str] = mapped_column(String(128))
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL")
    )
    rating: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    author_name: Mapped[str | None] = mapped_column(String(128))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_label: Mapped[str | None] = mapped_column(String(16))
    reply_draft: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None]
    published_at: Mapped[datetime]


class Ticket(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_shop_status", "shop_id", "status"),)

    channel: Mapped[TicketChannel] = mapped_column(str_enum(TicketChannel, "ticket_channel"))
    status: Mapped[TicketStatus] = mapped_column(
        str_enum(TicketStatus, "ticket_status"), default=TicketStatus.OPEN
    )
    subject: Mapped[str] = mapped_column(String(255))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    customer_email: Mapped[str | None] = mapped_column(String(320))
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reviews.id", ondelete="SET NULL")
    )
    priority: Mapped[int] = mapped_column(Integer, default=2)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    escalation_reason: Mapped[str | None] = mapped_column(String(255))
    last_message_at: Mapped[datetime | None]


class Message(UUIDPrimaryKey, Timestamps, ShopScoped, Base):
    __tablename__ = "messages"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[MessageDirection] = mapped_column(
        str_enum(MessageDirection, "message_direction")
    )
    author_type: Mapped[MessageAuthor] = mapped_column(str_enum(MessageAuthor, "message_author"))
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime | None]
