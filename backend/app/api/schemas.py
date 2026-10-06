import uuid
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import Plan, Role, ShopMode


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(default="", max_length=255)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    full_name: str


class ShopSummary(ApiModel):
    id: uuid.UUID
    name: str
    domain: str
    mode: ShopMode
    plan: Plan
    currency: str
    timezone: str
    role: Role


class MeResponse(BaseModel):
    user: UserOut
    shops: list[ShopSummary]
    csrf_token: str


class CreateDemoShopRequest(BaseModel):
    name: str = Field(default="Northbound Outdoor Gear", min_length=1, max_length=255)


class ShopSettingsOut(ApiModel):
    kill_switch: bool
    dry_run: bool
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    fraud_threshold: int
    max_discount_pct: int
    refund_ceiling_minor: int
    alert_email: str | None
    support_email: str | None
    sender_name: str | None
    physical_address: str | None


class ShopSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kill_switch: bool | None = None
    dry_run: bool | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    fraud_threshold: int | None = Field(default=None, ge=0, le=100)
    max_discount_pct: int | None = Field(default=None, ge=0, le=10)
    refund_ceiling_minor: int | None = Field(default=None, ge=0, le=1_000_000)
    alert_email: EmailStr | None = None
    support_email: EmailStr | None = None
    sender_name: str | None = Field(default=None, max_length=255)
    physical_address: str | None = Field(default=None, max_length=512)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    joined_at: datetime


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: Role = Role.VIEWER

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class UpdateMemberRequest(BaseModel):
    role: Role
