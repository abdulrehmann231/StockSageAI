import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_signup_returns_token_and_user(client: AsyncClient):
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
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["default_market"] == "PSX"
    assert body["user"]["risk_profile"] == "Moderate"
    assert "id" in body["user"]


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

    res = await client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "supersecret"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["access_token"]
    assert body["user"]["email"] == "bob@example.com"


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
async def test_me_with_invalid_token_returns_401(client: AsyncClient):
    res = await client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_current_user(client: AsyncClient):
    signup = await client.post(
        "/api/auth/signup",
        json={
            "email": "dave@example.com",
            "password": "supersecret",
            "full_name": "Dave",
        },
    )
    token = signup.json()["access_token"]

    res = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "dave@example.com"
    assert body["full_name"] == "Dave"
