"""Tests for the Phase-6 Reports persistence endpoints.

The orchestrator itself is stubbed; these tests assert the persistence layer:
- ``POST /api/reports/generate`` runs the orchestrator and writes a row owned
  by the current user,
- ``GET /api/reports/{id}`` is user-scoped (a second user gets 404),
- ``GET /api/reports/user`` lists rows most-recent-first with the slim view,
- ``404`` for unknown ticker, ``502`` when the orchestrator raises.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents import orchestrator
from agents.report_writer import StockReport
from db.models import Stock
from db.session import SessionLocal


async def _signup(client, email="reporter@example.com"):
    res = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "supersecret"},
    )
    assert res.status_code == 201


async def _seed_stock(ticker="ENGRO", market="PSX", currency="PKR"):
    async with SessionLocal() as session:
        session.add(Stock(ticker=ticker, name=f"{ticker} Inc.", market=market, currency=currency))
        await session.commit()


def _report(ticker="ENGRO", market="PSX") -> StockReport:
    return StockReport(
        ticker=ticker,
        market=market,
        company_name=f"{ticker} Inc.",
        verdict="BUY",
        confidence="medium",
        composite_score=0.42,
        executive_summary=f"{ticker}: bullish synthesis.",
        sources=["yfinance"],
        fetched_at=datetime.now(timezone.utc),
    )


def _patch_orchestrator(monkeypatch, *, raises: Exception | None = None):
    async def _stub(ticker, market, *, company_name=None, use_cache=True, max_news_articles=5):
        if raises:
            raise raises
        return _report(ticker, market)

    monkeypatch.setattr(orchestrator, "get_report", _stub)


@pytest.mark.asyncio
async def test_reports_generate_requires_auth(client):
    res = await client.post("/api/reports/generate", json={"ticker": "ENGRO"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_reports_generate_persists_row(client, monkeypatch):
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)

    res = await client.post("/api/reports/generate", json={"ticker": "engro"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["ticker"] == "ENGRO"
    assert body["market"] == "PSX"
    assert body["verdict"] == "BUY"
    assert body["confidence"] == "medium"
    assert body["report_data"]["executive_summary"].startswith("ENGRO:")
    assert "id" in body

    listing = (await client.get("/api/reports/user")).json()
    assert len(listing) == 1
    assert listing[0]["ticker"] == "ENGRO"
    # Slim list view does NOT include report_data.
    assert "report_data" not in listing[0]


@pytest.mark.asyncio
async def test_reports_generate_unknown_ticker_404(client, monkeypatch):
    await _signup(client)
    _patch_orchestrator(monkeypatch)
    res = await client.post("/api/reports/generate", json={"ticker": "NOPE"})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_reports_generate_orchestrator_failure_502(client, monkeypatch):
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch, raises=RuntimeError("agents down"))
    res = await client.post("/api/reports/generate", json={"ticker": "ENGRO"})
    assert res.status_code == 502


@pytest.mark.asyncio
async def test_reports_detail_returns_full_payload(client, monkeypatch):
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)

    created = (
        await client.post("/api/reports/generate", json={"ticker": "ENGRO"})
    ).json()
    detail = await client.get(f"/api/reports/{created['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["ticker"] == "ENGRO"
    assert body["report_data"]["executive_summary"]


@pytest.mark.asyncio
async def test_reports_detail_is_user_scoped(client, monkeypatch):
    """Bob asking for Alice's report → 404, not 403, to avoid existence leak."""
    await _seed_stock()
    _patch_orchestrator(monkeypatch)

    await _signup(client, email="alice@example.com")
    created = (
        await client.post("/api/reports/generate", json={"ticker": "ENGRO"})
    ).json()

    await client.post("/api/auth/logout")
    await _signup(client, email="bob@example.com")
    res = await client.get(f"/api/reports/{created['id']}")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_reports_user_list_sorted_desc(client, monkeypatch):
    await _signup(client)
    async with SessionLocal() as session:
        session.add_all(
            [
                Stock(ticker="ENGRO", name="Engro", market="PSX", currency="PKR"),
                Stock(ticker="AAPL", name="Apple", market="GLOBAL", currency="USD"),
            ]
        )
        await session.commit()
    _patch_orchestrator(monkeypatch)

    await client.post("/api/reports/generate", json={"ticker": "ENGRO"})
    await client.post("/api/reports/generate", json={"ticker": "AAPL"})

    listing = (await client.get("/api/reports/user")).json()
    assert [row["ticker"] for row in listing] == ["AAPL", "ENGRO"]
