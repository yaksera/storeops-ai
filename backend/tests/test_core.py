import httpx
import pytest

from app.auth.passwords import hash_password, verify_password
from app.core.crypto import TokenDecryptionError, decrypt_secret, encrypt_secret


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"]


async def test_ready_reports_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}


async def test_request_id_is_propagated(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


def test_password_hashing_roundtrip() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed != "correct horse battery"
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "wrong")
    assert not verify_password(None, "anything")
    assert not verify_password("not-a-hash", "anything")


def test_secret_encryption_roundtrip() -> None:
    ciphertext = encrypt_secret("shpat_example_token")
    assert b"shpat_example_token" not in ciphertext
    assert decrypt_secret(ciphertext) == "shpat_example_token"
    with pytest.raises(TokenDecryptionError):
        decrypt_secret(b"garbage")
