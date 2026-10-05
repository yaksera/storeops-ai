import pytest
from pydantic import SecretStr, ValidationError

from app.cli import main as cli_main
from app.core.config import Settings
from app.core.observability import _scrub, init_sentry, init_tracing


def test_production_rejects_unsafe_configuration() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(environment="production", _env_file=None)
    message = str(exc.value)
    assert "SECRET_KEY" in message
    assert "TOKEN_ENCRYPTION_KEY" in message
    assert "COOKIE_SECURE" in message


def test_production_accepts_safe_configuration() -> None:
    settings = Settings(
        environment="production",
        secret_key=SecretStr("x" * 48),
        token_encryption_key=SecretStr("k" * 44),
        cookie_secure=True,
        frontend_origin="https://app.storeops.example",
        _env_file=None,
    )
    assert settings.is_production


def test_observability_is_opt_in() -> None:
    settings = Settings(_env_file=None)
    assert init_sentry(settings, "api") is False
    assert init_tracing(settings, "api") is False


def test_error_events_are_scrubbed() -> None:
    event = {
        "request": {
            "headers": {"Cookie": "storeops_session=abc", "X-CSRF-Token": "t", "Accept": "json"},
            "cookies": {"storeops_session": "abc"},
            "data": {"email": "pat@customers.example"},
        }
    }
    scrubbed = _scrub(event, None)
    assert scrubbed["request"]["headers"]["Cookie"] == "[filtered]"
    assert scrubbed["request"]["headers"]["X-CSRF-Token"] == "[filtered]"
    assert scrubbed["request"]["headers"]["Accept"] == "json"
    assert "cookies" not in scrubbed["request"]
    assert "data" not in scrubbed["request"]


def test_cli_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        cli_main([])


def test_database_url_is_normalised_for_asyncpg() -> None:
    for raw in ("postgres://u:p@db:5432/x", "postgresql://u:p@db:5432/x"):
        settings = Settings(database_url=raw, _env_file=None)
        assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/x"
