"""Tests for the GET /api/news/{ticker} endpoint.

The News Agent itself is stubbed; these tests assert the HTTP contract: market/
company resolution from the stocks table, the ``refresh``/``limit`` query params
flowing through to the agent, 404 for unknown tickers, and 502 on agent failure.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents import news_agent
from agents.news_agent import NewsArticle, NewsImpact, NewsResult
from db.models import Stock
from db.session import SessionLocal


async def _seed(ticker="ENGRO", name="Engro Corporation Limited", market="PSX", currency="PKR"):
    async with SessionLocal() as session:
        session.add(Stock(ticker=ticker, name=name, market=market, currency=currency))
        await session.commit()


def _result(ticker="ENGRO", market="PSX"):
    article = NewsArticle(
        ticker=ticker,
        market=market,
        title="Engro posts record quarterly profit",
        url="https://example.com/engro-profit",
        source="Business Recorder",
        summary="Engro reported a record quarterly profit. Margins expanded on higher prices.",
        impact=NewsImpact.HIGH_POSITIVE,
        catalysts=["earnings"],
        relevance_score=4.2,
    )
    return NewsResult(
        ticker=ticker,
        market=market,
        company_name="Engro Corporation Limited",
        overall_news_sentiment=NewsImpact.MEDIUM_POSITIVE,
        top_catalyst="earnings",
        articles=[article],
        fetched_at=datetime.now(timezone.utc),
        sources=["Business Recorder"],
        cached=False,
    )


@pytest.mark.asyncio
async def test_news_endpoint_returns_articles(client, monkeypatch):
    await _seed()
    captured = {}

    async def _stub(ticker, market, *, company_name=None, max_articles=5, use_cache=True, **kwargs):
        captured.update(
            ticker=ticker,
            market=market,
            company_name=company_name,
            max_articles=max_articles,
            use_cache=use_cache,
        )
        return _result(ticker, market)

    monkeypatch.setattr(news_agent, "get_news", _stub)

    res = await client.get("/api/news/ENGRO")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ticker"] == "ENGRO"
    assert body["market"] == "PSX"
    assert body["top_catalyst"] == "earnings"
    assert len(body["articles"]) == 1
    assert body["articles"][0]["impact"] == "HIGH_POSITIVE"

    # Market + company resolved from the stocks table; cache on, default limit.
    assert captured["market"] == "PSX"
    assert captured["company_name"] == "Engro Corporation Limited"
    assert captured["use_cache"] is True
    assert captured["max_articles"] == 5


@pytest.mark.asyncio
async def test_news_endpoint_normalizes_ticker_case(client, monkeypatch):
    await _seed()

    async def _stub(ticker, market, **kwargs):
        return _result(ticker, market)

    monkeypatch.setattr(news_agent, "get_news", _stub)
    res = await client.get("/api/news/engro")
    assert res.status_code == 200, res.text
    assert res.json()["ticker"] == "ENGRO"


@pytest.mark.asyncio
async def test_news_endpoint_refresh_and_limit_flow_through(client, monkeypatch):
    await _seed()
    captured = {}

    async def _stub(ticker, market, *, company_name=None, max_articles=5, use_cache=True, **kwargs):
        captured.update(max_articles=max_articles, use_cache=use_cache)
        return _result(ticker, market)

    monkeypatch.setattr(news_agent, "get_news", _stub)
    res = await client.get("/api/news/ENGRO?refresh=true&limit=3")
    assert res.status_code == 200
    assert captured["use_cache"] is False
    assert captured["max_articles"] == 3


@pytest.mark.asyncio
async def test_news_endpoint_limit_out_of_range_422(client):
    await _seed()
    # limit must be 1..20.
    assert (await client.get("/api/news/ENGRO?limit=0")).status_code == 422
    assert (await client.get("/api/news/ENGRO?limit=99")).status_code == 422


@pytest.mark.asyncio
async def test_news_endpoint_unknown_ticker_404(client):
    res = await client.get("/api/news/NOPE")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_news_endpoint_agent_failure_502(client, monkeypatch):
    await _seed()

    async def _boom(*args, **kwargs):
        raise RuntimeError("all news sources down")

    monkeypatch.setattr(news_agent, "get_news", _boom)
    res = await client.get("/api/news/ENGRO")
    assert res.status_code == 502
