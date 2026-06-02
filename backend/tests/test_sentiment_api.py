"""Tests for the GET /api/sentiment/{ticker} endpoint.

The Sentiment Agent itself is stubbed; these tests assert the HTTP contract:
market resolution from the stocks table, 404 for unknown tickers, the
``refresh`` flag flowing through to ``use_cache``, and 502 on agent failure.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents import sentiment_agent
from agents.sentiment_agent import SentimentResult
from db.models import Stock
from db.session import SessionLocal


async def _seed(ticker="ENGRO", name="Engro Corporation Limited", market="PSX", currency="PKR"):
    async with SessionLocal() as session:
        session.add(Stock(ticker=ticker, name=name, market=market, currency=currency))
        await session.commit()


def _result(ticker="ENGRO", market="PSX"):
    return SentimentResult(
        ticker=ticker,
        market=market,
        company_name="Engro Corporation Limited",
        overall_sentiment=0.42,
        label="bullish",
        bullish_pct=71,
        bearish_pct=29,
        top_bullish_points=["record profit"],
        top_bearish_points=["high debt"],
        post_count=18,
        sources=["reddit", "telegram"],
        fetched_at=datetime.now(timezone.utc),
        cached=False,
    )


@pytest.mark.asyncio
async def test_sentiment_endpoint_returns_scored_result(client, monkeypatch):
    await _seed()

    captured = {}

    async def _stub(ticker, market, *, company_name=None, use_cache=True):
        captured.update(
            ticker=ticker, market=market, company_name=company_name, use_cache=use_cache
        )
        return _result(ticker, market)

    monkeypatch.setattr(sentiment_agent, "get_sentiment", _stub)

    res = await client.get("/api/sentiment/ENGRO")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ticker"] == "ENGRO"
    assert body["market"] == "PSX"
    assert body["label"] == "bullish"
    assert body["bullish_pct"] == 71
    assert set(body["sources"]) == {"reddit", "telegram"}

    # Market + company resolved from the stocks table; cache on by default.
    assert captured["market"] == "PSX"
    assert captured["company_name"] == "Engro Corporation Limited"
    assert captured["use_cache"] is True


@pytest.mark.asyncio
async def test_sentiment_endpoint_normalizes_ticker_case(client, monkeypatch):
    await _seed()

    async def _stub(ticker, market, **kwargs):
        return _result(ticker, market)

    monkeypatch.setattr(sentiment_agent, "get_sentiment", _stub)
    res = await client.get("/api/sentiment/engro")
    assert res.status_code == 200, res.text
    assert res.json()["ticker"] == "ENGRO"


@pytest.mark.asyncio
async def test_sentiment_endpoint_refresh_bypasses_cache(client, monkeypatch):
    await _seed()
    captured = {}

    async def _stub(ticker, market, *, company_name=None, use_cache=True):
        captured["use_cache"] = use_cache
        return _result(ticker, market)

    monkeypatch.setattr(sentiment_agent, "get_sentiment", _stub)
    res = await client.get("/api/sentiment/ENGRO?refresh=true")
    assert res.status_code == 200
    assert captured["use_cache"] is False


@pytest.mark.asyncio
async def test_sentiment_endpoint_unknown_ticker_404(client):
    res = await client.get("/api/sentiment/NOPE")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_sentiment_endpoint_agent_failure_502(client, monkeypatch):
    await _seed()

    async def _boom(*args, **kwargs):
        raise RuntimeError("scrapers all down")

    monkeypatch.setattr(sentiment_agent, "get_sentiment", _boom)
    res = await client.get("/api/sentiment/ENGRO")
    assert res.status_code == 502
