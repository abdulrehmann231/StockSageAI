"""Tests for the Phase-6 Alerts CRUD endpoints."""

from __future__ import annotations

import pytest

from db.models import Stock
from db.session import SessionLocal


async def _signup(client, email="alerter@example.com"):
    res = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "supersecret"},
    )
    assert res.status_code == 201


async def _seed_stocks():
    async with SessionLocal() as session:
        session.add_all(
            [
                Stock(ticker="AAPL", name="Apple Inc.", market="GLOBAL", currency="USD"),
                Stock(ticker="ENGRO", name="Engro Corporation Limited", market="PSX", currency="PKR"),
            ]
        )
        await session.commit()


@pytest.mark.asyncio
async def test_alerts_requires_auth(client):
    assert (await client.get("/api/alerts")).status_code == 401
    assert (
        await client.post(
            "/api/alerts",
            json={"ticker": "AAPL", "alert_type": "PRICE_DROP", "condition": {"threshold_pct": -5}},
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_create_price_drop_alert(client):
    await _signup(client)
    await _seed_stocks()
    res = await client.post(
        "/api/alerts",
        json={
            "ticker": "aapl",
            "alert_type": "PRICE_DROP",
            "condition": {"threshold_pct": -5},
            "cooldown_hours": 6,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["ticker"] == "AAPL"
    assert body["alert_type"] == "PRICE_DROP"
    assert body["condition"] == {"threshold_pct": -5}
    assert body["cooldown_hours"] == 6
    assert body["is_active"] is True
    assert body["last_triggered"] is None


@pytest.mark.asyncio
async def test_create_alert_unknown_ticker_404(client):
    await _signup(client)
    res = await client.post(
        "/api/alerts",
        json={
            "ticker": "NOPE",
            "alert_type": "PRICE_DROP",
            "condition": {"threshold_pct": -5},
        },
    )
    assert res.status_code == 404


@pytest.mark.parametrize(
    "alert_type,bad_condition",
    [
        ("PRICE_DROP", {"threshold_pct": 5}),  # must be negative
        ("PRICE_DROP", {"threshold_pct": "down"}),  # wrong type
        ("PRICE_RISE", {"threshold_pct": -5}),  # must be positive
        ("PRICE_TARGET", {"target": -1, "direction": "above"}),  # target must be positive
        ("PRICE_TARGET", {"target": 100, "direction": "sideways"}),  # bad direction
        ("BIG_NEWS", {"impacts": ["MEDIUM_OK"]}),  # invalid impact label
        ("SENTIMENT_SHIFT", {"to": "ecstatic"}),  # invalid label
    ],
)
@pytest.mark.asyncio
async def test_create_alert_invalid_condition_422(client, alert_type, bad_condition):
    await _signup(client)
    await _seed_stocks()
    res = await client.post(
        "/api/alerts",
        json={"ticker": "AAPL", "alert_type": alert_type, "condition": bad_condition},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_list_alerts_is_user_scoped(client):
    await _seed_stocks()

    await _signup(client, email="alice@example.com")
    await client.post(
        "/api/alerts",
        json={"ticker": "AAPL", "alert_type": "PRICE_DROP", "condition": {"threshold_pct": -5}},
    )
    assert len((await client.get("/api/alerts")).json()) == 1

    await client.post("/api/auth/logout")
    await _signup(client, email="bob@example.com")
    assert (await client.get("/api/alerts")).json() == []


@pytest.mark.asyncio
async def test_patch_alert_toggles_active_and_updates_condition(client):
    await _signup(client)
    await _seed_stocks()
    created = (
        await client.post(
            "/api/alerts",
            json={
                "ticker": "AAPL",
                "alert_type": "PRICE_TARGET",
                "condition": {"target": 200, "direction": "above"},
            },
        )
    ).json()

    res = await client.patch(
        f"/api/alerts/{created['id']}",
        json={"is_active": False, "condition": {"target": 220, "direction": "above"}},
    )
    assert res.status_code == 200
    assert res.json()["is_active"] is False
    assert res.json()["condition"] == {"target": 220, "direction": "above"}


@pytest.mark.asyncio
async def test_patch_alert_invalid_new_condition_422(client):
    await _signup(client)
    await _seed_stocks()
    created = (
        await client.post(
            "/api/alerts",
            json={
                "ticker": "AAPL",
                "alert_type": "PRICE_DROP",
                "condition": {"threshold_pct": -5},
            },
        )
    ).json()
    res = await client.patch(
        f"/api/alerts/{created['id']}", json={"condition": {"threshold_pct": 5}}
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_delete_alert(client):
    await _signup(client)
    await _seed_stocks()
    created = (
        await client.post(
            "/api/alerts",
            json={
                "ticker": "AAPL",
                "alert_type": "PRICE_DROP",
                "condition": {"threshold_pct": -5},
            },
        )
    ).json()
    assert (await client.delete(f"/api/alerts/{created['id']}")).status_code == 204
    again = await client.delete(f"/api/alerts/{created['id']}")
    assert again.status_code == 404
