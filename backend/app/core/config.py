from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value comes from the environment (or a local .env file)."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+asyncpg://storeops:storeops@localhost:5432/storeops"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: SecretStr = SecretStr("dev-insecure-secret-change-me")
    token_encryption_key: SecretStr | None = Field(
        default=None,
        description="Fernet key used to encrypt Shopify access tokens at rest.",
    )

    frontend_origin: str = "http://localhost:3000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    session_cookie_name: str = "storeops_session"
    csrf_cookie_name: str = "storeops_csrf"
    session_ttl_seconds: int = 60 * 60 * 24 * 14
    cookie_secure: bool = False

    auth_rate_limit_per_minute: int = 10

    demo_mode_enabled: bool = True
    simulator_tick_seconds: float = 3.0

    sse_heartbeat_seconds: float = 15.0
    event_stream_maxlen: int = 1000

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
