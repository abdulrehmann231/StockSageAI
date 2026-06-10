"""Tests for the GET /api/report/{ticker} endpoint.

The orchestrator itself is stubbed; these tests assert the HTTP contract:
market/company resolution from the stocks table, ``refresh`` and
``max_news_articles`` flowing through to the orchestrator, 404 for unknown
tickers, and 502 on orchestrator failure.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents import orchestrator
from agents.report_writer import StockReport
from db.models import Stock
from db.session import SessionLocal


async def _seed(ticker="ENGRO", name="Engro Corporation Limited", market="PSX", currency="PKR"):
    async with SessionLocal() as session:
        session.add(Stock(ticker=ticker, name=name, market=market, currency=currency))
        await session.commit()


def _report(ticker="ENGRO", market="PSX") -> StockReport:
    return StockReport(
        ticker=ticker,
        market=market,
        company_name="Engro Corporation Limited",
        verdict="BUY",
        confidence="medium",
        composite_score=0.42,
        executive_summary="Engro posted record profit; sentiment is bullish; price up 1.2%.",
        price_summary="Price: 485.38 PKR. Change: +7.10 (+1.48%).",
        news_summary="Overall news tone: medium positive. Top catalyst: earnings.",
        sentiment_summary="Label: bullish. Score: +0.42 on a -1..+1 scale.",
        key_catalysts=["earnings"],
        risks=[],
        opportunities=["record profit"],
        sources=["psx", "Business Recorder", "reddit"],
        errors=[],
        fetched_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_report_endpoint_returns_synthesised_report(client, monkeypatch):
    await _seed()
    captured = {}

    async def _stub(ticker, market, *, company_name=None, use_cache=True, max_news_articles=5):
        captured.update(
            ticker=ticker,
            market=market,
            company_name=company_name,
            use_cache=use_cache,
            max_news_articles=max_news_articles,
        )
        return _report(ticker, market)

    monkeypatch.setattr(orchestrator, "get_report", _stub)

    res = await client.get("/api/report/ENGRO")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ticker"] == "ENGRO"
    assert body["market"] == "PSX"
    assert body["verdict"] == "BUY"
    assert body["confidence"] == "medium"
    assert body["composite_score"] == 0.42
    assert body["key_catalysts"] == ["earnings"]
    assert "psx" in body["sources"]

    # Market + company resolved from the stocks table; cache on; default limit.
    assert captured["market"] == "PSX"
    assert captured["company_name"] == "Engro Corporation Limited"
    assert captured["use_cache"] is True
    assert captured["max_news_articles"] == 5


@pytest.mark.asyncio
async def test_report_endpoint_normalizes_ticker_case(client, monkeypatch):
    await _seed()

    async def _stub(ticker, market, **kwargs):
        return _report(ticker, market)

    monkeypatch.setattr(orchestrator, "get_report", _stub)
    res = await client.get("/api/report/engro")
    assert res.status_code == 200, res.text
    assert res.json()["ticker"] == "ENGRO"


@pytest.mark.asyncio
async def test_report_endpoint_refresh_and_limit_flow_through(client, monkeypatch):
    await _seed()
    captured = {}

    async def _stub(ticker, market, *, company_name=None, use_cache=True, max_news_articles=5):
        captured.update(use_cache=use_cache, max_news_articles=max_news_articles)
        return _report(ticker, market)

    monkeypatch.setattr(orchestrator, "get_report", _stub)
    res = await client.get("/api/report/ENGRO?refresh=true&max_news_articles=8")
    assert res.status_code == 200
    assert captured["use_cache"] is False
    assert captured["max_news_articles"] == 8


@pytest.mark.asyncio
async def test_report_endpoint_max_articles_out_of_range_422(client):
    await _seed()
    assert (await client.get("/api/report/ENGRO?max_news_articles=0")).status_code == 422
    assert (await client.get("/api/report/ENGRO?max_news_articles=99")).status_code == 422


@pytest.mark.asyncio
async def test_report_endpoint_unknown_ticker_404(client):
    res = await client.get("/api/report/NOPE")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_report_endpoint_agent_failure_502(client, monkeypatch):
    await _seed()

    async def _boom(*args, **kwargs):
        raise RuntimeError("orchestrator collapsed")

    monkeypatch.setattr(orchestrator, "get_report", _boom)
    res = await client.get("/api/report/ENGRO")
    assert res.status_code == 502
