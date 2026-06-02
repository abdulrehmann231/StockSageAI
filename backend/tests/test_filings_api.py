"""Tests for the GET /api/filings/{ticker} endpoint.

The Filings agent is stubbed; these assert the HTTP contract: market/company
resolution from the stocks table, the ``refresh`` flag flowing through as
``use_cache=False``, 404 for unknown tickers, and 502 on agent failure.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from agents import filings_agent
from db.models import Stock
from db.schemas import FilingAnswer, FilingsResult
from db.session import SessionLocal
from main import app


async def _seed(ticker="AAPL", name="Apple Inc.", market="GLOBAL", currency="USD"):
    async with SessionLocal() as session:
        session.add(Stock(ticker=ticker, name=name, market=market, currency=currency))
        await session.commit()


def _result(ticker="AAPL", market="GLOBAL"):
    return FilingsResult(
        ticker=ticker,
        market=market,
        company_name="Apple Inc.",
        answers=[
            FilingAnswer(
                question="What is the company's recent revenue trend and growth?",
                answer="Revenue grew 18% (10-K FY2023, p.42).",
                grounded=True,
            )
        ],
        chunks_indexed=25,
        fetched_at=datetime.now(timezone.utc),
        cached=False,
    )


async def _client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_returns_filings_result(monkeypatch):
    await _seed()

    async def _stub(ticker, market, *, company_name=None, db=None, use_cache=True):
        return _result(ticker, market)

    monkeypatch.setattr(filings_agent, "get_filings_analysis", _stub)

    async with await _client() as client:
        resp = await client.get("/api/filings/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert body["chunks_indexed"] == 25
    assert body["answers"][0]["grounded"] is True


async def test_ticker_is_case_normalized(monkeypatch):
    await _seed()
    seen = {}

    async def _stub(ticker, market, *, company_name=None, db=None, use_cache=True):
        seen["ticker"] = ticker
        return _result(ticker, market)

    monkeypatch.setattr(filings_agent, "get_filings_analysis", _stub)

    async with await _client() as client:
        resp = await client.get("/api/filings/aapl")
    assert resp.status_code == 200
    assert seen["ticker"] == "AAPL"


async def test_refresh_flag_disables_cache(monkeypatch):
    await _seed()
    seen = {}

    async def _stub(ticker, market, *, company_name=None, db=None, use_cache=True):
        seen["use_cache"] = use_cache
        return _result(ticker, market)

    monkeypatch.setattr(filings_agent, "get_filings_analysis", _stub)

    async with await _client() as client:
        resp = await client.get("/api/filings/AAPL?refresh=true")
    assert resp.status_code == 200
    assert seen["use_cache"] is False


async def test_unknown_ticker_404(monkeypatch):
    async with await _client() as client:
        resp = await client.get("/api/filings/NOPE")
    assert resp.status_code == 404


async def test_agent_failure_502(monkeypatch):
    await _seed()

    async def _boom(ticker, market, *, company_name=None, db=None, use_cache=True):
        raise RuntimeError("vector store down")

    monkeypatch.setattr(filings_agent, "get_filings_analysis", _boom)

    async with await _client() as client:
        resp = await client.get("/api/filings/AAPL")
    assert resp.status_code == 502
