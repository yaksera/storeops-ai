from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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

    shopify_api_key: str | None = None
    shopify_api_secret: SecretStr | None = None
    shopify_api_version: str = "2025-10"
    shopify_scopes: str = (
        "read_orders,write_orders,read_products,write_products,read_inventory,write_inventory,"
        "read_customers,read_fulfillments,read_checkouts,write_discounts,"
        "read_merchant_managed_fulfillment_orders,write_merchant_managed_fulfillment_orders"
    )
    # Public URL Shopify redirects to and posts webhooks to (the web origin proxies /api).
    public_app_url: str | None = None

    resend_api_key: SecretStr | None = None
    email_from: str = "StoreOps AI <alerts@storeops.example>"

    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_default_model: str = "google/gemini-2.5-flash"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2

    agent_timeout_seconds: float = 30.0

    # Queue this worker consumes. Pro stores' events go to the priority queue, served by a
    # dedicated worker so a busy store on another plan can't delay them.
    worker_queue: str = "arq:queue"
    priority_queue: str = "storeops:priority"

    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.05
    otel_exporter_otlp_endpoint: str | None = None
    release: str | None = None

    sse_heartbeat_seconds: float = 15.0
    event_stream_maxlen: int = 1000

    @model_validator(mode="after")
    def _production_safety(self) -> "Settings":
        if self.environment != "production":
            return self
        problems = []
        if (
            self.secret_key.get_secret_value() in {"dev-insecure-secret-change-me", ""}
            or len(self.secret_key.get_secret_value()) < 32
        ):
            problems.append("SECRET_KEY must be a random value of at least 32 characters")
        if self.token_encryption_key is None:
            problems.append("TOKEN_ENCRYPTION_KEY is required")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true (HTTPS)")
        if not self.frontend_origin.startswith("https://"):
            problems.append("FRONTEND_ORIGIN must be an https:// URL")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @field_validator("database_url", mode="before")
    @classmethod
    def _asyncpg_url(cls, value: object) -> object:
        """Accept the plain `postgres://` URLs hosting providers hand out."""
        if isinstance(value, str):
            for prefix in ("postgres://", "postgresql://"):
                if value.startswith(prefix):
                    return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def app_url(self) -> str:
        return (self.public_app_url or self.frontend_origin).rstrip("/")

    @property
    def shopify_configured(self) -> bool:
        return bool(self.shopify_api_key and self.shopify_api_secret)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
