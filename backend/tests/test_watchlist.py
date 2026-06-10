"""Tests for the Phase-6 watchlist endpoints.

Covers:
- auth gating (no cookie → 401),
- list returns stocks ordered by most-recently-added with enriched fields,
- POST is idempotent (re-adding returns 200 with the existing row),
- POST 404 on unknown ticker,
- DELETE 204 on success, 404 when the row is not on the watchlist.
"""

from __future__ import annotations

import pytest

from db.models import Stock
from db.session import SessionLocal


async def _signup(client, email="watcher@example.com"):
    res = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "supersecret"},
    )
    assert res.status_code == 201, res.text
    return res.json()["user"]


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
async def test_watchlist_requires_auth(client):
    assert (await client.get("/api/watchlist")).status_code == 401
    assert (
        await client.post("/api/watchlist", json={"ticker": "AAPL"})
    ).status_code == 401
    assert (await client.delete("/api/watchlist/AAPL")).status_code == 401


@pytest.mark.asyncio
async def test_watchlist_post_then_list_returns_enriched_row(client):
    await _signup(client)
    await _seed_stocks()

    res = await client.post("/api/watchlist", json={"ticker": "aapl"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["ticker"] == "AAPL"
    assert body["name"] == "Apple Inc."
    assert body["market"] == "GLOBAL"
    assert body["currency"] == "USD"

    listing = (await client.get("/api/watchlist")).json()
    assert len(listing) == 1
    assert listing[0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_watchlist_post_is_idempotent(client):
    await _signup(client)
    await _seed_stocks()

    first = await client.post("/api/watchlist", json={"ticker": "AAPL"})
    assert first.status_code == 201

    second = await client.post("/api/watchlist", json={"ticker": "AAPL"})
    assert second.status_code == 200
    assert second.json()["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_watchlist_post_unknown_ticker_404(client):
    await _signup(client)
    await _seed_stocks()

    res = await client.post("/api/watchlist", json={"ticker": "NOPE"})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_watchlist_listing_orders_most_recently_added_first(client):
    await _signup(client)
    await _seed_stocks()

    await client.post("/api/watchlist", json={"ticker": "AAPL"})
    await client.post("/api/watchlist", json={"ticker": "ENGRO"})

    listing = (await client.get("/api/watchlist")).json()
    assert [row["ticker"] for row in listing] == ["ENGRO", "AAPL"]


@pytest.mark.asyncio
async def test_watchlist_delete_204_then_404(client):
    await _signup(client)
    await _seed_stocks()
    await client.post("/api/watchlist", json={"ticker": "AAPL"})

    delete = await client.delete("/api/watchlist/AAPL")
    assert delete.status_code == 204

    # Second delete now fails because the row is gone.
    again = await client.delete("/api/watchlist/AAPL")
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_watchlist_is_user_scoped(client):
    """Two users cannot see each other's watchlists."""
    await _seed_stocks()

    await _signup(client, email="alice@example.com")
    await client.post("/api/watchlist", json={"ticker": "AAPL"})

    # Switch to a different user.
    await client.post("/api/auth/logout")
    await _signup(client, email="bob@example.com")
    listing = (await client.get("/api/watchlist")).json()
    assert listing == []
