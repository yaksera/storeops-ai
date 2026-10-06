import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Enum, ForeignKey, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JSONType = JSON().with_variant(JSONB(), "postgresql")
ShopifyId = BigInteger


def utcnow() -> datetime:
    return datetime.now(UTC)


def str_enum(enum_cls: type[enum.StrEnum], name: str) -> Enum:
    """Portable enum column: VARCHAR plus CHECK constraint, values stored as-is."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONType,
        list[Any]: JSONType,
    }


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=utcnow, onupdate=utcnow
    )


class ShopScoped:
    """Every tenant-owned row carries `shop_id`; queries must always filter on it."""

    @declared_attr
    @classmethod
    def shop_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
