"""Tests for the Phase 5 orchestrator.

Covers:
- parallel fan-out actually runs the three agents concurrently (no serial wait),
- one agent failing does not sink the run; its error is recorded and its payload
  becomes None,
- ``use_cache=False`` bypasses the Redis cache and forces a fresh fan-out,
- cache hits short-circuit the fan-out and flip ``cached=True`` on the response.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agents import news_agent, orchestrator, price_agent, sentiment_agent
from agents.news_agent import NewsArticle, NewsImpact, NewsResult
from agents.report_writer import StockReport
from agents.sentiment_agent import SentimentResult
from db.schemas import PriceQuote
from services import cache_service


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _price(ticker: str = "AAPL", market: str = "GLOBAL") -> PriceQuote:
    return PriceQuote(
        ticker=ticker,
        market=market,
        currency="USD",
        price=150.0,
        previous_close=148.0,
        open=148.5,
        day_high=151.0,
        day_low=147.5,
        volume=1_000_000,
        week_52_high=200.0,
        week_52_low=100.0,
        market_cap=2.5e12,
        pe_ratio=28.0,
        change=2.0,
        change_pct=1.35,
        fetched_at=datetime.now(timezone.utc),
        source="yfinance",
    )


def _news(ticker: str = "AAPL", market: str = "GLOBAL") -> NewsResult:
    article = NewsArticle(
        ticker=ticker,
        market=market,
        title="Apple earnings beat",
        url="https://example.com/a",
        source="Yahoo Finance",
        published_at=datetime.now(timezone.utc),
        summary="Apple beat estimates. Services revenue accelerated.",
        impact=NewsImpact.MEDIUM_POSITIVE,
        catalysts=["earnings"],
        relevance_score=4.0,
    )
    return NewsResult(
        ticker=ticker,
        market=market,
        company_name="Apple Inc.",
        overall_news_sentiment=NewsImpact.MEDIUM_POSITIVE,
        top_catalyst="earnings",
        articles=[article],
        fetched_at=datetime.now(timezone.utc),
        sources=["Yahoo Finance"],
    )


def _sentiment(ticker: str = "AAPL", market: str = "GLOBAL") -> SentimentResult:
    return SentimentResult(
        ticker=ticker,
        market=market,
        company_name="Apple Inc.",
        overall_sentiment=0.4,
        label="bullish",
        bullish_pct=70,
        bearish_pct=30,
        top_bullish_points=["services growth"],
        top_bearish_points=["China weakness"],
        post_count=25,
        sources=["reddit", "stocktwits"],
        fetched_at=datetime.now(timezone.utc),
    )


def _patch_agents(
    monkeypatch,
    *,
    price=None,
    news=None,
    sentiment=None,
    delay_s: float = 0.0,
):
    """Replace the three agent calls with stubs that optionally sleep."""

    async def _stub_price(ticker, market, *, use_cache=True):
        if delay_s:
            await asyncio.sleep(delay_s)
        if isinstance(price, Exception):
            raise price
        return price if price is not None else _price(ticker, market)

    async def _stub_news(ticker, market, *, company_name=None, max_articles=5, use_cache=True, **kw):
        if delay_s:
            await asyncio.sleep(delay_s)
        if isinstance(news, Exception):
            raise news
        return news if news is not None else _news(ticker, market)

    async def _stub_sentiment(ticker, market, *, company_name=None, use_cache=True):
        if delay_s:
            await asyncio.sleep(delay_s)
        if isinstance(sentiment, Exception):
            raise sentiment
        return sentiment if sentiment is not None else _sentiment(ticker, market)

    monkeypatch.setattr(price_agent, "get_price", _stub_price)
    monkeypatch.setattr(news_agent, "get_news", _stub_news)
    monkeypatch.setattr(sentiment_agent, "get_sentiment", _stub_sentiment)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_orchestrator_runs_agents_in_parallel(monkeypatch):
    """All three agents sleeping 200ms each must finish in <~350ms total."""
    _patch_agents(monkeypatch, delay_s=0.2)

    started = asyncio.get_event_loop().time()
    report = await orchestrator.get_report("AAPL", "GLOBAL", company_name="Apple Inc.")
    elapsed = asyncio.get_event_loop().time() - started

    # Serial would be ~0.6s. Generous bound to keep CI flake-resistant.
    assert elapsed < 0.45, f"orchestrator ran serially: {elapsed:.3f}s"
    assert isinstance(report, StockReport)
    assert report.price is not None
    assert report.news is not None
    assert report.sentiment is not None
    assert report.errors == []


@pytest.mark.asyncio
async def test_orchestrator_isolates_single_agent_failure(monkeypatch):
    _patch_agents(monkeypatch, sentiment=RuntimeError("scraper down"))

    report = await orchestrator.get_report("AAPL", "GLOBAL", company_name="Apple Inc.")
    assert report.price is not None
    assert report.news is not None
    assert report.sentiment is None
    assert any("sentiment" in err and "scraper down" in err for err in report.errors)


@pytest.mark.asyncio
async def test_orchestrator_handles_all_agents_failing(monkeypatch):
    _patch_agents(
        monkeypatch,
        price=ValueError("upstream 502"),
        news=TimeoutError("read timeout"),
        sentiment=RuntimeError("scrapers all down"),
    )

    report = await orchestrator.get_report("AAPL", "GLOBAL", company_name="Apple Inc.")
    assert report.price is None
    assert report.news is None
    assert report.sentiment is None
    # We still produce a (low-confidence) HOLD report.
    assert report.verdict == "HOLD"
    assert report.confidence == "low"
    assert len(report.errors) == 3


@pytest.mark.asyncio
async def test_orchestrator_use_cache_false_bypasses_redis(monkeypatch):
    """A cached payload exists but ``use_cache=False`` forces a fresh fan-out."""
    _patch_agents(monkeypatch)

    fake = {
        "ticker": "AAPL",
        "market": "GLOBAL",
        "executive_summary": "stale summary",
        "verdict": "SELL",
        "confidence": "high",
        "composite_score": -0.9,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    await cache_service.set_json(f"{orchestrator.CACHE_PREFIX}GLOBAL:AAPL", fake, ttl_seconds=60)

    fresh = await orchestrator.get_report(
        "AAPL", "GLOBAL", company_name="Apple Inc.", use_cache=False
    )
    assert fresh.executive_summary != "stale summary"
    assert fresh.cached is False
    assert fresh.verdict != "SELL"


@pytest.mark.asyncio
async def test_orchestrator_cache_hit_short_circuits_fanout(monkeypatch):
    """First call populates cache; second call returns cached=True without re-running."""
    _patch_agents(monkeypatch)

    first = await orchestrator.get_report("AAPL", "GLOBAL", company_name="Apple Inc.")
    assert first.cached is False

    # Replace agent stubs with bombs — if cache works, none are called.
    async def _boom(*args, **kwargs):
        raise AssertionError("agent should not run on cache hit")

    monkeypatch.setattr(price_agent, "get_price", _boom)
    monkeypatch.setattr(news_agent, "get_news", _boom)
    monkeypatch.setattr(sentiment_agent, "get_sentiment", _boom)

    second = await orchestrator.get_report("AAPL", "GLOBAL", company_name="Apple Inc.")
    assert second.cached is True
    assert second.executive_summary == first.executive_summary
    assert second.verdict == first.verdict


@pytest.mark.asyncio
async def test_orchestrator_normalizes_ticker_and_market(monkeypatch):
    """Lowercase / whitespace inputs are normalized; cache key matches."""
    captured = {}

    async def _stub_price(ticker, market, *, use_cache=True):
        captured["price"] = (ticker, market)
        return _price(ticker, market)

    async def _stub_news(ticker, market, *, company_name=None, max_articles=5, use_cache=True, **kw):
        captured["news"] = (ticker, market)
        return _news(ticker, market)

    async def _stub_sentiment(ticker, market, *, company_name=None, use_cache=True):
        captured["sentiment"] = (ticker, market)
        return _sentiment(ticker, market)

    monkeypatch.setattr(price_agent, "get_price", _stub_price)
    monkeypatch.setattr(news_agent, "get_news", _stub_news)
    monkeypatch.setattr(sentiment_agent, "get_sentiment", _stub_sentiment)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = await orchestrator.get_report("  aapl  ", "global", company_name="Apple Inc.")
    assert captured["price"] == ("AAPL", "GLOBAL")
    assert captured["news"] == ("AAPL", "GLOBAL")
    assert captured["sentiment"] == ("AAPL", "GLOBAL")
    assert report.ticker == "AAPL"
    assert report.market == "GLOBAL"


@pytest.mark.asyncio
async def test_report_orchestrator_state_wrapper_exposes_agent_payloads(monkeypatch):
    """LangGraph-style wrapper populates report_data + per-agent payloads."""
    _patch_agents(monkeypatch)
    state = {
        "ticker": "AAPL",
        "market": "GLOBAL",
        "company_name": "Apple Inc.",
        "use_cache": False,
    }
    next_state = await orchestrator.report_orchestrator(state)
    assert next_state["ticker"] == "AAPL"
    assert "report_data" in next_state
    assert "price_data" in next_state
    assert "news_data" in next_state
    assert "sentiment_data" in next_state
    assert next_state["report_data"]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_orchestrator_cache_read_failure_does_not_block(monkeypatch):
    _patch_agents(monkeypatch)

    async def _bad_get(_key):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache_service, "get_json", _bad_get)
    report = await orchestrator.get_report("AAPL", "GLOBAL", company_name="Apple Inc.")
    assert report.cached is False
    assert report.price is not None


@pytest.mark.asyncio
async def test_orchestrator_cache_write_failure_does_not_block(monkeypatch):
    _patch_agents(monkeypatch)

    async def _bad_set(_key, _value, ttl_seconds):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache_service, "set_json", _bad_set)
    report = await orchestrator.get_report("AAPL", "GLOBAL", company_name="Apple Inc.")
    # Despite cache write failure, the orchestrator still produced a report.
    assert report.cached is False
    assert report.price is not None
