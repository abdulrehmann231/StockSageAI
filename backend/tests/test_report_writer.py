"""Tests for the Report Writer agent (Phase 5).

Covers:
- the deterministic verdict + composite-score derivation across the signal
  permutations (all three, partial, none),
- LLM-payload validation, clamping, and alias coercion so a malformed model
  response can't poison the report,
- structural invariants (sources deduped, risks/opportunities capped, raw
  agent payloads echoed through).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.news_agent import NewsArticle, NewsImpact, NewsResult
from agents.report_writer import (
    StockReport,
    _coerce_confidence,
    _coerce_verdict,
    _composite_score,
    _derive_verdict,
    write_report,
)
from agents.sentiment_agent import SentimentResult
from db.schemas import PriceQuote


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _price(change_pct: float = 1.0, *, ticker: str = "AAPL", market: str = "GLOBAL") -> PriceQuote:
    price = 150.0
    previous = price / (1 + change_pct / 100)
    return PriceQuote(
        ticker=ticker,
        market=market,
        currency="USD",
        price=price,
        previous_close=previous,
        open=previous,
        day_high=price * 1.01,
        day_low=previous * 0.99,
        volume=1_000_000,
        week_52_high=200.0,
        week_52_low=100.0,
        market_cap=2.5e12,
        pe_ratio=28.0,
        eps=5.0,
        dividend_yield=0.5,
        change=price - previous,
        change_pct=change_pct,
        fetched_at=datetime.now(timezone.utc),
        source="yfinance",
    )


def _news(
    *,
    impact: NewsImpact = NewsImpact.MEDIUM_POSITIVE,
    article_impacts: list[NewsImpact] | None = None,
    ticker: str = "AAPL",
    market: str = "GLOBAL",
) -> NewsResult:
    article_impacts = article_impacts or [impact, NewsImpact.NEUTRAL]
    articles = [
        NewsArticle(
            ticker=ticker,
            market=market,
            title=f"Headline {i}",
            url=f"https://example.com/article-{i}",
            source="Yahoo Finance",
            published_at=datetime.now(timezone.utc) - timedelta(hours=i),
            summary=f"Summary {i} about the company.",
            impact=art_impact,
            catalysts=["earnings"] if i == 0 else [],
            relevance_score=3.0 + i,
        )
        for i, art_impact in enumerate(article_impacts)
    ]
    return NewsResult(
        ticker=ticker,
        market=market,
        company_name="Apple Inc.",
        overall_news_sentiment=impact,
        top_catalyst="earnings",
        articles=articles,
        fetched_at=datetime.now(timezone.utc),
        sources=["Yahoo Finance"],
    )


def _sentiment(
    *,
    overall: float = 0.4,
    label: str = "bullish",
    bullish_pct: int = 70,
    bearish_pct: int = 30,
    post_count: int = 25,
    ticker: str = "AAPL",
    market: str = "GLOBAL",
) -> SentimentResult:
    return SentimentResult(
        ticker=ticker,
        market=market,
        company_name="Apple Inc.",
        overall_sentiment=overall,
        label=label,
        bullish_pct=bullish_pct,
        bearish_pct=bearish_pct,
        top_bullish_points=["strong iPhone demand", "services growth"],
        top_bearish_points=["China weakness"],
        post_count=post_count,
        sources=["reddit", "stocktwits"],
        fetched_at=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Composite scoring
# --------------------------------------------------------------------------- #


def test_composite_score_combines_all_three_channels():
    score, weight = _composite_score(
        price=_price(change_pct=2.0),
        news=_news(impact=NewsImpact.MEDIUM_POSITIVE),
        sentiment=_sentiment(overall=0.5),
    )
    assert weight == pytest.approx(1.0)
    assert 0.1 < score < 0.9


def test_composite_score_handles_zero_inputs():
    score, weight = _composite_score(price=None, news=None, sentiment=None)
    assert score == 0.0
    assert weight == 0.0


def test_composite_score_ignores_empty_news_and_zero_post_sentiment():
    empty_news = NewsResult(
        ticker="AAPL",
        market="GLOBAL",
        articles=[],
        fetched_at=datetime.now(timezone.utc),
    )
    zero_post_sentiment = _sentiment(post_count=0)
    score, weight = _composite_score(
        price=_price(change_pct=5.0), news=empty_news, sentiment=zero_post_sentiment
    )
    # Only the price channel should have contributed (weight 0.20).
    assert weight == pytest.approx(0.20)
    assert 0.4 < score < 0.6


def test_composite_score_caps_extreme_price_swings():
    score_extreme, _ = _composite_score(
        price=_price(change_pct=50.0), news=None, sentiment=None
    )
    score_capped, _ = _composite_score(
        price=_price(change_pct=10.0), news=None, sentiment=None
    )
    assert score_extreme == pytest.approx(score_capped)
    assert score_extreme == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Verdict + confidence derivation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "composite,expected",
    [
        (0.8, "BUY"),
        (0.3, "ACCUMULATE"),
        (0.0, "HOLD"),
        (-0.3, "REDUCE"),
        (-0.8, "SELL"),
    ],
)
def test_derive_verdict_thresholds(composite: float, expected: str):
    verdict, _ = _derive_verdict(composite, weight=1.0)
    assert verdict == expected


def test_derive_confidence_scales_with_signal_weight():
    _, low = _derive_verdict(0.3, weight=0.2)
    _, mid = _derive_verdict(0.3, weight=0.45)
    _, high = _derive_verdict(0.3, weight=0.9)
    assert (low, mid, high) == ("low", "medium", "high")


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [
        ("BUY", "BUY"),
        ("strong_buy", "BUY"),
        ("strong sell", "SELL"),
        ("outperform", "ACCUMULATE"),
        ("underweight", "REDUCE"),
        ("market perform", "HOLD"),
        ("garbage", "HOLD"),
        (None, "HOLD"),
    ],
)
def test_coerce_verdict_alias_and_fallback(value, expected):
    assert _coerce_verdict(value, fallback="HOLD") == expected


@pytest.mark.parametrize(
    "value,expected",
    [("low", "low"), ("MEDIUM", "medium"), ("moderate", "medium"), ("bogus", "low"), (5, "low")],
)
def test_coerce_confidence(value, expected):
    assert _coerce_confidence(value, fallback="low") == expected


# --------------------------------------------------------------------------- #
# Public write_report — deterministic path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_write_report_deterministic_path_when_llm_absent(monkeypatch):
    """No OPENROUTER_API_KEY → deterministic verdict, no model_used."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        price=_price(change_pct=2.5),
        news=_news(impact=NewsImpact.MEDIUM_POSITIVE),
        sentiment=_sentiment(overall=0.5),
    )
    assert isinstance(report, StockReport)
    assert report.model_used is None
    assert report.verdict in ("BUY", "ACCUMULATE")
    assert report.confidence == "high"  # all three channels contributing
    assert 0.1 < report.composite_score < 0.9
    assert report.key_catalysts == ["earnings"]
    assert "Apple Inc." in report.executive_summary
    assert report.price_summary and "USD" in report.price_summary
    assert report.news_summary and "Headline" in report.news_summary
    assert report.sentiment_summary and "bullish" in report.sentiment_summary
    assert set(report.sources) >= {"yfinance", "Yahoo Finance", "reddit", "stocktwits"}


@pytest.mark.asyncio
async def test_write_report_handles_only_price(monkeypatch):
    """Sentiment + news missing → still get a HOLD/REDUCE report from price alone."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name=None,
        price=_price(change_pct=-0.5),
        news=None,
        sentiment=None,
    )
    assert report.confidence == "low"
    assert report.verdict == "HOLD"
    assert report.news_summary is None
    assert report.sentiment_summary is None
    assert report.price_summary is not None
    assert report.key_catalysts == []


@pytest.mark.asyncio
async def test_write_report_records_inbound_errors(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        price=_price(change_pct=1.0),
        news=None,
        sentiment=None,
        errors=["news: TimeoutError", "sentiment: RuntimeError(boom)"],
    )
    assert report.errors == ["news: TimeoutError", "sentiment: RuntimeError(boom)"]


@pytest.mark.asyncio
async def test_write_report_negative_news_pulls_down_to_sell(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    bear_news = _news(
        impact=NewsImpact.HIGH_NEGATIVE,
        article_impacts=[
            NewsImpact.HIGH_NEGATIVE,
            NewsImpact.HIGH_NEGATIVE,
            NewsImpact.MEDIUM_NEGATIVE,
        ],
    )
    bear_sentiment = _sentiment(
        overall=-0.8,
        label="bearish",
        bullish_pct=20,
        bearish_pct=80,
    )
    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        price=_price(change_pct=-5.0),
        news=bear_news,
        sentiment=bear_sentiment,
    )
    assert report.verdict == "SELL"
    assert report.composite_score < -0.5
    assert any("Headline" in risk for risk in report.risks)
    assert any("Recent price drawdown" in risk for risk in report.risks)


@pytest.mark.asyncio
async def test_write_report_dedupes_sources(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    # Both news and sentiment claim "yahoo" as a source — should appear once.
    news = _news()
    news.sources = ["yfinance", "Yahoo Finance"]
    sentiment = _sentiment()
    sentiment.sources = ["yfinance", "reddit"]
    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        price=_price(),
        news=news,
        sentiment=sentiment,
    )
    assert report.sources.count("yfinance") == 1


# --------------------------------------------------------------------------- #
# Public write_report — LLM path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_write_report_llm_path_overrides_verdict(monkeypatch):
    """LLM returns a clean response → verdict, narrative, model_used adopted."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    captured: dict = {}

    async def _stub(*, payload):
        captured["payload"] = payload
        return {
            "verdict": "BUY",
            "confidence": "high",
            "executive_summary": "LLM-generated synthesis sentence.",
            "price_summary": "LLM price section.",
            "news_summary": "LLM news section.",
            "sentiment_summary": "LLM sentiment section.",
            "risks": ["macro headwinds", "macro headwinds", "supply chain"],
            "opportunities": ["AI tailwinds"],
            "_model": "test/model:free",
        }

    from services import llm_service

    monkeypatch.setattr(llm_service, "synthesize_report", _stub)

    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        price=_price(change_pct=1.0),
        news=_news(),
        sentiment=_sentiment(),
    )
    assert report.verdict == "BUY"
    assert report.confidence == "high"
    assert report.executive_summary == "LLM-generated synthesis sentence."
    assert report.price_summary == "LLM price section."
    assert report.news_summary == "LLM news section."
    assert report.sentiment_summary == "LLM sentiment section."
    # Duplicates removed, max-4 cap applied.
    assert report.risks == ["macro headwinds", "supply chain"]
    assert report.opportunities == ["AI tailwinds"]
    assert report.model_used == "test/model:free"
    # Payload condensed correctly.
    assert captured["payload"]["ticker"] == "AAPL"
    assert captured["payload"]["price"]["price"] == 150.0
    assert captured["payload"]["news"]["overall_tone"] == "MEDIUM_POSITIVE"


@pytest.mark.asyncio
async def test_write_report_llm_returning_none_falls_back(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from services import llm_service

    async def _none(*, payload):
        return None

    monkeypatch.setattr(llm_service, "synthesize_report", _none)
    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        price=_price(change_pct=2.0),
        news=_news(),
        sentiment=_sentiment(),
    )
    assert report.model_used is None
    assert "Apple Inc." in report.executive_summary


@pytest.mark.asyncio
async def test_write_report_llm_missing_summary_falls_back(monkeypatch):
    """LLM omits executive_summary → deterministic path still produces a report."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from services import llm_service

    async def _no_summary(*, payload):
        return {"verdict": "BUY", "confidence": "high"}

    monkeypatch.setattr(llm_service, "synthesize_report", _no_summary)
    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        price=_price(change_pct=1.0),
        news=_news(),
        sentiment=_sentiment(),
    )
    # We don't accept the LLM verdict when its summary was unusable.
    assert report.model_used is None
    assert report.executive_summary  # deterministic summary populated


@pytest.mark.asyncio
async def test_write_report_llm_garbage_verdict_falls_back_to_baseline(monkeypatch):
    """LLM returns nonsense verdict + summary → keep summary, fall back on verdict."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from services import llm_service

    async def _bad_verdict(*, payload):
        return {
            "verdict": "TO_THE_MOON",
            "confidence": "ultra-high",
            "executive_summary": "valid summary",
            "risks": "not a list",
            "opportunities": [],
            "_model": "test/model:free",
        }

    monkeypatch.setattr(llm_service, "synthesize_report", _bad_verdict)
    report = await write_report(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        price=_price(change_pct=1.0),
        news=_news(),
        sentiment=_sentiment(),
    )
    assert report.executive_summary == "valid summary"
    assert report.verdict in ("BUY", "ACCUMULATE", "HOLD", "REDUCE", "SELL")
    assert report.confidence in ("low", "medium", "high")
    assert report.risks == []  # invalid type silently dropped
