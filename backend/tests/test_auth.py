import httpx
import pytest
from fastapi import FastAPI

from tests.conftest import Session, signup


async def test_signup_sets_httponly_session_cookie(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"email": "Ada@Northbound.example", "password": "correct horse battery"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ada@northbound.example"
    assert body["shops"] == []
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("storeops_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("storeops_csrf="))
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "HttpOnly" not in csrf_cookie


async def test_signup_rejects_duplicate_email(app: FastAPI, owner: Session) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as other:
        response = await other.post(
            "/api/auth/signup",
            json={"email": "OWNER@northbound.example", "password": "another long password"},
        )
    assert response.status_code == 409


async def test_signup_validates_password_length(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/signup", json={"email": "short@northbound.example", "password": "short"}
    )
    assert response.status_code == 422


async def test_login_and_me(app: FastAPI, owner: Session) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as browser:
        bad = await browser.post(
            "/api/auth/login",
            json={"email": "owner@northbound.example", "password": "wrong password"},
        )
        assert bad.status_code == 401
        unknown = await browser.post(
            "/api/auth/login",
            json={"email": "nobody@northbound.example", "password": "whatever"},
        )
        assert unknown.status_code == 401
        assert unknown.json()["detail"] == bad.json()["detail"]

        ok = await browser.post(
            "/api/auth/login",
            json={"email": "owner@northbound.example", "password": "correct horse battery"},
        )
        assert ok.status_code == 200
        me = await browser.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["email"] == "owner@northbound.example"
        assert me.json()["csrf_token"] == ok.json()["csrf_token"]


async def test_me_requires_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401
    client.cookies.set("storeops_session", "forged")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_unsafe_requests_require_csrf_token(owner: Session) -> None:
    response = await owner.client.post("/api/shops/demo", json={})
    assert response.status_code == 403
    response = await owner.client.post(
        "/api/shops/demo", json={}, headers={"x-csrf-token": "not-the-token"}
    )
    assert response.status_code == 403
    response = await owner.post("/api/shops/demo", json={})
    assert response.status_code == 201


async def test_foreign_origin_is_rejected(owner: Session) -> None:
    response = await owner.client.post(
        "/api/shops/demo",
        json={},
        headers={"x-csrf-token": owner.csrf, "origin": "https://evil.example"},
    )
    assert response.status_code == 403


async def test_logout_revokes_session(owner: Session) -> None:
    response = await owner.post("/api/auth/logout")
    assert response.status_code == 204
    assert (await owner.get("/api/auth/me")).status_code == 401


@pytest.mark.parametrize("attempts", [3])
async def test_login_is_rate_limited(
    app: FastAPI, attempts: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_rate_limit_per_minute", attempts)
    session = await signup(app, "limited@northbound.example")
    statuses = []
    for _ in range(attempts + 1):
        response = await session.client.post(
            "/api/auth/login", json={"email": "limited@northbound.example", "password": "nope"}
        )
        statuses.append(response.status_code)
    await session.client.aclose()
    assert statuses[-1] == 429
    assert statuses[:-1] == [401] * attempts
