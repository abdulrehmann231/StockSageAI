import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_signup_sets_cookie_and_returns_user(client: AsyncClient):
    res = await client.post(
        "/api/auth/signup",
        json={
            "email": "alice@example.com",
            "password": "supersecret",
            "full_name": "Alice",
            "default_market": "PSX",
            "risk_profile": "Moderate",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["default_market"] == "PSX"
    assert body["user"]["risk_profile"] == "Moderate"
    assert "id" in body["user"]
    # Token must NOT be in the response body (it lives in the httpOnly cookie).
    assert "access_token" not in body
    # Cookie was set on the client by the response.
    assert client.cookies.get("access_token") is not None


@pytest.mark.asyncio
async def test_signup_duplicate_email_returns_409(client: AsyncClient):
    payload = {"email": "dup@example.com", "password": "supersecret"}
    first = await client.post("/api/auth/signup", json=payload)
    assert first.status_code == 201

    second = await client.post("/api/auth/signup", json=payload)
    assert second.status_code == 409
    assert "already registered" in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_signup_rejects_short_password(client: AsyncClient):
    res = await client.post(
        "/api/auth/signup",
        json={"email": "shortpw@example.com", "password": "abc"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_signup_rejects_invalid_market(client: AsyncClient):
    res = await client.post(
        "/api/auth/signup",
        json={
            "email": "badmarket@example.com",
            "password": "supersecret",
            "default_market": "MARS",
        },
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_login_with_valid_credentials(client: AsyncClient):
    await client.post(
        "/api/auth/signup",
        json={"email": "bob@example.com", "password": "supersecret"},
    )
    # Sign-out so subsequent login truly tests the cookie roundtrip.
    client.cookies.clear()

    res = await client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "supersecret"},
    )
    assert res.status_code == 200
    assert res.json()["user"]["email"] == "bob@example.com"
    assert client.cookies.get("access_token") is not None


@pytest.mark.asyncio
async def test_login_with_wrong_password_returns_401(client: AsyncClient):
    await client.post(
        "/api/auth/signup",
        json={"email": "carol@example.com", "password": "supersecret"},
    )

    res = await client.post(
        "/api/auth/login",
        json={"email": "carol@example.com", "password": "wrong-password"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_login_with_unknown_email_returns_401(client: AsyncClient):
    res = await client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": "supersecret"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_token(client: AsyncClient):
    res = await client.get("/api/auth/me")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_me_with_invalid_bearer_token_returns_401(client: AsyncClient):
    res = await client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_current_user_via_cookie(client: AsyncClient):
    """Cookie set on signup should auto-authenticate /me on the same client."""
    await client.post(
        "/api/auth/signup",
        json={
            "email": "dave@example.com",
            "password": "supersecret",
            "full_name": "Dave",
        },
    )

    res = await client.get("/api/auth/me")
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "dave@example.com"
    assert body["full_name"] == "Dave"


@pytest.mark.asyncio
async def test_me_works_with_bearer_fallback(client: AsyncClient):
    """Bearer token is still accepted as a fallback for API tooling."""
    # Sign up via one client (sets cookie), then read the JWT off the cookie
    # to use it as a bearer on a fresh client without cookies.
    await client.post(
        "/api/auth/signup",
        json={"email": "eve@example.com", "password": "supersecret"},
    )
    token = client.cookies.get("access_token")
    assert token is not None

    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as bare:
        res = await bare.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.json()["email"] == "eve@example.com"


@pytest.mark.asyncio
async def test_logout_clears_cookie(client: AsyncClient):
    await client.post(
        "/api/auth/signup",
        json={"email": "frank@example.com", "password": "supersecret"},
    )
    assert client.cookies.get("access_token") is not None

    res = await client.post("/api/auth/logout")
    assert res.status_code == 204
    assert client.cookies.get("access_token") is None

    # /me should now reject the request.
    res = await client.get("/api/auth/me")
    assert res.status_code == 401


@pytest_asyncio.fixture
async def tight_rate_limit(monkeypatch):
    """Lower the auth rate limit and reset slowapi for one test."""
    from core.config import get_settings
    from core.limiter import limiter

    monkeypatch.setenv("AUTH_RATE_LIMIT", "3/minute")
    get_settings.cache_clear()
    limiter.reset()
    yield
    monkeypatch.setenv("AUTH_RATE_LIMIT", os.environ.get("AUTH_RATE_LIMIT", "1000/minute"))
    get_settings.cache_clear()
    limiter.reset()


@pytest.mark.asyncio
async def test_login_rate_limit_returns_429(client: AsyncClient, tight_rate_limit):
    payload = {"email": "ratelimited@example.com", "password": "supersecret"}
    statuses = []
    for _ in range(5):
        res = await client.post("/api/auth/login", json=payload)
        statuses.append(res.status_code)
    # First 3 requests pass through (and 401 because user doesn't exist);
    # the 4th and 5th should be rate-limited.
    assert statuses[:3] == [401, 401, 401], statuses
    assert 429 in statuses[3:], statuses
