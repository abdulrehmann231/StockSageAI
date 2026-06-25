"""Offline tests for the Phase-7 portfolio API endpoints + snapshot worker.

Auth uses the signup-cookie flow (same as the watchlist tests). The Price Agent
is stubbed at the service layer so no test touches the network.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from db.models import Stock
from db.schemas import PriceQuote
from db.session import SessionLocal
from services import portfolio_service
from workers import portfolio_snapshot


async def _signup(client, email="port@example.com"):
    res = await client.post(
        "/api/auth/signup", json={"email": email, "password": "supersecret"}
    )
    assert res.status_code == 201, res.text
    return res.json()["user"]


async def _seed_stocks():
    async with SessionLocal() as session:
        session.add_all(
            [
                Stock(ticker="AAPL", name="Apple Inc.", market="NASDAQ",
                      sector="Technology", currency="USD"),
                Stock(ticker="ENGRO", name="Engro Corporation Limited", market="PSX",
                      sector="Conglomerate", currency="PKR"),
            ]
        )
        await session.commit()


@pytest.fixture
def stub_prices(monkeypatch):
    prices = {"AAPL": 150.0, "ENGRO": 500.0}

    async def fake_get_price(ticker, market, *, use_cache=True):
        return PriceQuote(
            ticker=ticker, market=market, price=prices.get(ticker, 100.0),
            fetched_at=datetime.now(timezone.utc), source="test",
        )

    monkeypatch.setattr(portfolio_service.price_agent, "get_price", fake_get_price)
    return prices


# --------------------------------------------------------------------------- #
# Auth gating
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_portfolio_requires_auth(client):
    assert (await client.get("/api/portfolio")).status_code == 401
    assert (
        await client.post("/api/portfolio/holdings", json={"ticker": "AAPL"})
    ).status_code == 401
    assert (await client.get("/api/portfolio/tax-estimate")).status_code == 401


# --------------------------------------------------------------------------- #
# Holdings CRUD
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_add_holding_logs_buy_transaction(client, stub_prices):
    await _signup(client)
    await _seed_stocks()

    res = await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "aapl", "quantity": 10, "avg_buy_price": 100.0,
              "buy_date": "2026-01-01"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["ticker"] == "AAPL"
    assert body["cost_basis"] == 1000.0

    txns = (await client.get("/api/portfolio/transactions")).json()
    assert len(txns) == 1
    assert txns[0]["transaction_type"] == "BUY"
    assert txns[0]["quantity"] == 10


@pytest.mark.asyncio
async def test_add_holding_unknown_ticker_404(client):
    await _signup(client)
    res = await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "NOPE", "quantity": 1, "avg_buy_price": 1.0},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_add_holding_rejects_bad_quantity(client):
    await _signup(client)
    await _seed_stocks()
    res = await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "AAPL", "quantity": -5, "avg_buy_price": 100.0},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_portfolio_returns_live_pnl(client, stub_prices):
    await _signup(client)
    await _seed_stocks()
    await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "AAPL", "quantity": 10, "avg_buy_price": 100.0},
    )
    res = await client.get("/api/portfolio")
    assert res.status_code == 200
    body = res.json()
    assert body["metrics"]["total_value"] == 1500.0
    assert body["metrics"]["total_gain_loss"] == 500.0
    assert body["holdings"][0]["gain_loss_pct"] == 50.0


@pytest.mark.asyncio
async def test_patch_and_delete_holding(client, stub_prices):
    await _signup(client)
    await _seed_stocks()
    created = (await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "AAPL", "quantity": 10, "avg_buy_price": 100.0},
    )).json()
    hid = created["id"]

    patched = await client.patch(
        f"/api/portfolio/holdings/{hid}", json={"quantity": 20}
    )
    assert patched.status_code == 200
    assert patched.json()["quantity"] == 20
    assert patched.json()["cost_basis"] == 2000.0

    deleted = await client.delete(f"/api/portfolio/holdings/{hid}")
    assert deleted.status_code == 204
    assert (await client.delete(f"/api/portfolio/holdings/{hid}")).status_code == 404


@pytest.mark.asyncio
async def test_holdings_are_user_scoped(client, stub_prices):
    await _signup(client, email="alice@example.com")
    await _seed_stocks()
    created = (await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "AAPL", "quantity": 10, "avg_buy_price": 100.0},
    )).json()

    # Bob signs in (overwrites cookie) and cannot see/delete Alice's holding.
    await _signup(client, email="bob@example.com")
    assert (await client.get("/api/portfolio")).json()["holdings"] == []
    assert (
        await client.delete(f"/api/portfolio/holdings/{created['id']}")
    ).status_code == 404


# --------------------------------------------------------------------------- #
# Transactions + CSV
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_transaction_csv_export(client, stub_prices):
    await _signup(client)
    await _seed_stocks()
    await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "AAPL", "quantity": 10, "avg_buy_price": 100.0,
              "buy_date": "2026-01-01"},
    )
    res = await client.get("/api/portfolio/transactions/export.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "transaction_date,ticker,type" in res.text
    assert "AAPL,BUY,10" in res.text.replace(" ", "")


# --------------------------------------------------------------------------- #
# Tax estimate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tax_estimate_endpoint(client, stub_prices):
    await _signup(client)
    await _seed_stocks()
    await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "AAPL", "quantity": 10, "avg_buy_price": 100.0,
              "buy_date": "2026-01-01"},
    )
    res = await client.get("/api/portfolio/tax-estimate")
    assert res.status_code == 200
    body = res.json()
    assert len(body["lots"]) == 1
    # gain 500, short-term US 22% → 110
    assert body["lots"][0]["estimated_tax"] == 110.0


# --------------------------------------------------------------------------- #
# Analyze + performance
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_analyze_empty_portfolio_400(client):
    await _signup(client)
    assert (await client.post("/api/portfolio/analyze")).status_code == 400


@pytest.mark.asyncio
async def test_analyze_persists_and_latest(client, stub_prices, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _signup(client)
    await _seed_stocks()
    await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "AAPL", "quantity": 10, "avg_buy_price": 100.0},
    )
    res = await client.post("/api/portfolio/analyze")
    assert res.status_code == 201, res.text
    body = res.json()
    assert 0 <= body["health_score"] <= 100
    assert "summary" in body["analysis_data"]

    latest = await client.get("/api/portfolio/analyses/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_performance_validates_range(client):
    await _signup(client)
    assert (await client.get("/api/portfolio/performance?range=bogus")).status_code == 422
    ok = await client.get("/api/portfolio/performance?range=30d")
    assert ok.status_code == 200
    assert ok.json() == {"range": "30d", "points": []}


# --------------------------------------------------------------------------- #
# Snapshot worker
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_snapshot_worker_writes_and_powers_performance(client, stub_prices):
    user = await _signup(client)
    await _seed_stocks()
    await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "AAPL", "quantity": 10, "avg_buy_price": 100.0},
    )

    written = await portfolio_snapshot.run_portfolio_snapshots(
        snapshot_date=date(2026, 6, 25)
    )
    assert written == 1

    # Re-running the same day upserts rather than duplicating.
    written2 = await portfolio_snapshot.run_portfolio_snapshots(
        snapshot_date=date(2026, 6, 25)
    )
    assert written2 == 1

    res = await client.get("/api/portfolio/performance?range=all")
    points = res.json()["points"]
    assert len(points) == 1
    assert points[0]["total_value"] == 1500.0
