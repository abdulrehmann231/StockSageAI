"""Tests for the Sentiment Agent.

Covers the pure scoring/classification helpers, the source-aggregation and
caching behaviour of ``get_sentiment``, the LLM path with validation/fallback,
source-failure isolation, and the LangGraph node wrapper. Source fetchers and
the LLM call are stubbed so the suite is deterministic and offline.

A single live test (``@pytest.mark.live``) hits the real StockTwits API and is
deselected by default.
"""

from __future__ import annotations

import math

import pytest

from agents import sentiment_agent
from agents.sentiment_agent import (
    SentimentResult,
    _bucket_label,
    _coerce_llm_scores,
    _deterministic_score,
    classify_post,
    get_sentiment,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _post(text, *, source="reddit", label=None, score=0, pid=None):
    return {
        "source": source,
        "id": pid or f"{source}:{abs(hash(text)) % 10_000_000}",
        "text": text,
        "created_at": None,
        "author": "tester",
        "url": None,
        "label": label,
        "score": score,
    }


BULL_1 = _post("Strong earnings, this is a buy, going to moon 🚀", score=50)
BULL_2 = _post("Bullish breakout incoming, upgrade and accumulate", score=30)
BEAR_1 = _post("Overvalued, I'm shorting this, loading puts", score=40)
NEUTRAL_1 = _post("Anyone know when the next earnings call is scheduled?")


@pytest.fixture
def no_llm(monkeypatch):
    """Force the deterministic path by making the LLM scorer return None."""

    async def _none(**_kwargs):
        return None

    monkeypatch.setattr(sentiment_agent.llm_service, "analyze_sentiment_posts", _none)


def _stub_sources(monkeypatch, *, reddit=None, stocktwits=None, reddit_exc=None, stocktwits_exc=None):
    """Replace the agent's source fetchers with deterministic stubs."""
    calls = {"reddit": 0, "stocktwits": 0}

    async def _reddit(ticker, market, company_name):
        calls["reddit"] += 1
        if reddit_exc is not None:
            raise reddit_exc
        return list(reddit or [])

    async def _stocktwits(ticker, market, company_name):
        calls["stocktwits"] += 1
        if stocktwits_exc is not None:
            raise stocktwits_exc
        return list(stocktwits or [])

    monkeypatch.setattr(sentiment_agent, "fetch_reddit_sentiment", _reddit)
    monkeypatch.setattr(sentiment_agent, "fetch_stocktwits_sentiment", _stocktwits)
    return calls


# --------------------------------------------------------------------------- #
# Pure helpers — classification
# --------------------------------------------------------------------------- #


def test_classify_provider_label_wins_over_keywords():
    # Text reads bearish, but the author explicitly tagged it Bullish.
    assert classify_post("this is going to crash and dump", provider_label="bullish") == "bullish"
    assert classify_post("to the moon, strong buy", provider_label="bearish") == "bearish"


def test_classify_keyword_bullish():
    assert classify_post("strong buy, breakout rally incoming") == "bullish"


def test_classify_keyword_bearish():
    assert classify_post("overvalued, shorting with puts, expecting a crash") == "bearish"


def test_classify_neutral_when_balanced_or_empty():
    assert classify_post("not sure what to think about this one") == "neutral"
    assert classify_post("buy or sell? equal pros and cons, calls vs puts") == "neutral"
    assert classify_post("") == "neutral"


@pytest.mark.parametrize(
    "text",
    [
        "death cross forming",          # "ath" must not match inside "death"
        "I would rather wait for earnings",  # "ath" inside "rather"
        "glossy quarterly report",       # "loss" inside "glossy"
        "the analyst dismissed the call",  # "miss" inside "dismissed"
        "watched the rugby highlights",  # "rug" inside "rugby"
    ],
)
def test_classify_no_substring_false_positives(text):
    # Single-word lexicon terms must match whole tokens only, never substrings
    # of unrelated words. These would all have skewed to bullish/bearish under
    # a raw ``term in lowered`` test.
    assert classify_post(text) == "neutral"


def test_classify_death_cross_is_not_bullish():
    # Regression: "death cross" previously matched "ath" → bullish.
    assert classify_post("death cross forming") != "bullish"


def test_classify_multiword_and_emoji_terms_still_match():
    # Phrases and emoji never appear as plain word tokens, so they rely on the
    # substring path — make sure that still fires.
    assert classify_post("time to buy the dip here") == "bullish"
    assert classify_post("loading up 🚀🚀") == "bullish"
    assert classify_post("printing an all-time high today") == "bullish"


@pytest.mark.parametrize(
    "score,expected",
    [(0.5, "bullish"), (0.15, "bullish"), (0.0, "neutral"), (-0.14, "neutral"), (-0.15, "bearish"), (-1.0, "bearish")],
)
def test_bucket_label_thresholds(score, expected):
    assert _bucket_label(score) == expected


# --------------------------------------------------------------------------- #
# Pure helpers — deterministic scoring
# --------------------------------------------------------------------------- #


def test_deterministic_score_matches_plan_example_shape():
    scores = _deterministic_score([BULL_1, BULL_2, BEAR_1])
    assert scores["bullish_pct"] == 67
    assert scores["bearish_pct"] == 33
    assert math.isclose(scores["overall_sentiment"], 0.333, abs_tol=0.01)
    assert scores["bullish_pct"] + scores["bearish_pct"] == 100


def test_deterministic_score_empty_is_neutral():
    scores = _deterministic_score([])
    assert scores["overall_sentiment"] == 0.0
    assert scores["bullish_pct"] == 0
    assert scores["bearish_pct"] == 0
    assert scores["top_bullish_points"] == []
    assert scores["top_bearish_points"] == []


def test_deterministic_score_neutral_posts_dont_count_as_directional():
    scores = _deterministic_score([BULL_1, NEUTRAL_1])
    # One bullish, one neutral → 100% of directional posts are bullish.
    assert scores["bullish_pct"] == 100
    assert scores["bearish_pct"] == 0
    assert scores["overall_sentiment"] == 1.0


def test_deterministic_points_ranked_by_score_and_deduped():
    dup_a = _post("buy buy buy strong rally", score=5)
    dup_b = _post("buy buy buy strong rally", score=99)  # same text, higher score
    top = _post("undervalued breakout, accumulate now", score=100)
    scores = _deterministic_score([dup_a, dup_b, top])
    # Highest-score unique snippet first; duplicates collapsed.
    assert len(scores["top_bullish_points"]) == 2
    assert scores["top_bullish_points"][0].lower().startswith("undervalued")


# --------------------------------------------------------------------------- #
# Pure helpers — LLM payload validation
# --------------------------------------------------------------------------- #


def test_coerce_valid_llm_payload():
    out = _coerce_llm_scores(
        {
            "overall_sentiment": 0.34,
            "bullish_pct": 67,
            "bearish_pct": 33,
            "top_bullish_points": ["earnings beat", "buybacks"],
            "top_bearish_points": ["high debt"],
        }
    )
    assert out["overall_sentiment"] == 0.34
    assert out["bullish_pct"] == 67
    assert out["bearish_pct"] == 33
    assert out["top_bullish_points"] == ["earnings beat", "buybacks"]


def test_coerce_clamps_and_renormalizes():
    out = _coerce_llm_scores(
        {"overall_sentiment": 2.5, "bullish_pct": 80, "bearish_pct": 40}
    )
    assert out["overall_sentiment"] == 1.0  # clamped to +1
    assert out["bullish_pct"] + out["bearish_pct"] == 100  # renormalized


def test_coerce_backfills_missing_pct():
    out = _coerce_llm_scores({"overall_sentiment": -0.2, "bearish_pct": 70})
    assert out["bullish_pct"] == 30
    assert out["bearish_pct"] == 70


def test_coerce_rejects_nonnumeric_and_nan():
    assert _coerce_llm_scores({"overall_sentiment": "lots"}) is None
    assert _coerce_llm_scores({"overall_sentiment": float("nan"), "bullish_pct": 50}) is None
    assert _coerce_llm_scores("not a dict") is None
    # No usable percentages at all.
    assert _coerce_llm_scores({"overall_sentiment": 0.1}) is None


def test_coerce_filters_nonstring_points_and_caps_at_three():
    out = _coerce_llm_scores(
        {
            "overall_sentiment": 0.1,
            "bullish_pct": 55,
            "bearish_pct": 45,
            "top_bullish_points": ["a", "", 123, "b", "c", "d"],
            "top_bearish_points": "not a list",
        }
    )
    assert out["top_bullish_points"] == ["a", "b", "c"]
    assert out["top_bearish_points"] == []


# --------------------------------------------------------------------------- #
# get_sentiment — aggregation, caching, routing  (async; uses real Redis)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_sentiment_aggregates_both_sources_deterministic(no_llm, monkeypatch):
    calls = _stub_sources(
        monkeypatch,
        reddit=[BULL_1, BULL_2],
        stocktwits=[BEAR_1],
    )
    result = await get_sentiment("AAPL", "GLOBAL", use_cache=False)

    assert isinstance(result, SentimentResult)
    assert result.post_count == 3
    assert result.bullish_pct == 67
    assert result.bearish_pct == 33
    assert result.label == "bullish"
    assert set(result.sources) == {"reddit", "stocktwits"}
    assert calls == {"reddit": 1, "stocktwits": 1}


@pytest.mark.asyncio
async def test_get_sentiment_respects_provider_labels(no_llm, monkeypatch):
    # Text looks bearish, but the StockTwits label says bullish — label wins.
    labeled = _post("crash incoming, dumping everything", source="stocktwits", label="bullish")
    _stub_sources(monkeypatch, reddit=[], stocktwits=[labeled])
    result = await get_sentiment("AAPL", "GLOBAL", use_cache=False)
    assert result.bullish_pct == 100
    assert result.overall_sentiment == 1.0


@pytest.mark.asyncio
async def test_get_sentiment_uses_llm_when_available(monkeypatch):
    _stub_sources(monkeypatch, reddit=[BULL_1], stocktwits=[BEAR_1])

    async def _stub_llm(**_kwargs):
        return {
            "overall_sentiment": -0.5,
            "bullish_pct": 25,
            "bearish_pct": 75,
            "top_bullish_points": ["new product line"],
            "top_bearish_points": ["margin compression", "debt load"],
        }

    monkeypatch.setattr(sentiment_agent.llm_service, "analyze_sentiment_posts", _stub_llm)
    result = await get_sentiment("AAPL", "GLOBAL", use_cache=False)
    assert result.overall_sentiment == -0.5
    assert result.bearish_pct == 75
    assert result.label == "bearish"
    assert result.top_bearish_points == ["margin compression", "debt load"]


@pytest.mark.asyncio
async def test_get_sentiment_falls_back_when_llm_payload_unusable(monkeypatch):
    _stub_sources(monkeypatch, reddit=[BULL_1, BULL_2], stocktwits=[BEAR_1])

    async def _bad_llm(**_kwargs):
        return {"garbage": True}

    monkeypatch.setattr(sentiment_agent.llm_service, "analyze_sentiment_posts", _bad_llm)
    result = await get_sentiment("AAPL", "GLOBAL", use_cache=False)
    # Deterministic numbers, plus an error breadcrumb.
    assert result.bullish_pct == 67
    assert any("unusable" in e for e in result.errors)


@pytest.mark.asyncio
async def test_get_sentiment_backfills_points_when_llm_omits_them(monkeypatch):
    _stub_sources(monkeypatch, reddit=[BULL_1, BULL_2], stocktwits=[BEAR_1])

    async def _llm_no_points(**_kwargs):
        return {
            "overall_sentiment": 0.3,
            "bullish_pct": 67,
            "bearish_pct": 33,
            "top_bullish_points": [],
            "top_bearish_points": [],
        }

    monkeypatch.setattr(sentiment_agent.llm_service, "analyze_sentiment_posts", _llm_no_points)
    result = await get_sentiment("AAPL", "GLOBAL", use_cache=False)
    assert result.overall_sentiment == 0.3  # LLM score kept
    assert result.top_bullish_points  # backfilled from posts
    assert result.top_bearish_points


@pytest.mark.asyncio
async def test_get_sentiment_caches_second_call(no_llm, monkeypatch):
    calls = _stub_sources(monkeypatch, reddit=[BULL_1], stocktwits=[BEAR_1])

    first = await get_sentiment("AAPL", "GLOBAL")
    assert first.cached is False
    assert calls["reddit"] == 1

    second = await get_sentiment("AAPL", "GLOBAL")
    assert second.cached is True
    assert second.bullish_pct == first.bullish_pct
    # Sources were not hit again.
    assert calls["reddit"] == 1
    assert calls["stocktwits"] == 1


@pytest.mark.asyncio
async def test_get_sentiment_use_cache_false_bypasses(no_llm, monkeypatch):
    calls = _stub_sources(monkeypatch, reddit=[BULL_1], stocktwits=[BEAR_1])
    await get_sentiment("AAPL", "GLOBAL")  # populate cache
    await get_sentiment("AAPL", "GLOBAL", use_cache=False)  # bypass
    assert calls["reddit"] == 2


@pytest.mark.asyncio
async def test_get_sentiment_no_posts_returns_neutral(no_llm, monkeypatch):
    _stub_sources(monkeypatch, reddit=[], stocktwits=[])
    result = await get_sentiment("ZZZZ", "GLOBAL", use_cache=False)
    assert result.post_count == 0
    assert result.overall_sentiment == 0.0
    assert result.label == "neutral"
    assert result.bullish_pct == 0
    assert result.sources == []


@pytest.mark.asyncio
async def test_get_sentiment_isolates_source_failure(no_llm, monkeypatch):
    _stub_sources(
        monkeypatch,
        reddit_exc=RuntimeError("reddit 503"),
        stocktwits=[BULL_1, BULL_2],
    )
    result = await get_sentiment("AAPL", "GLOBAL", use_cache=False)
    assert result.post_count == 2
    assert result.sources == ["stocktwits"]
    assert any("reddit" in e for e in result.errors)


@pytest.mark.asyncio
async def test_psx_market_skips_stocktwits(no_llm, monkeypatch):
    calls = _stub_sources(monkeypatch, reddit=[BULL_1], stocktwits=[BEAR_1])
    result = await get_sentiment("ENGRO", "PSX", use_cache=False)
    assert calls["reddit"] == 1
    assert calls["stocktwits"] == 0  # not part of the PSX source set
    assert result.sources == ["reddit"]


@pytest.mark.asyncio
async def test_get_sentiment_normalizes_ticker(no_llm, monkeypatch):
    _stub_sources(monkeypatch, reddit=[BULL_1], stocktwits=[])
    result = await get_sentiment("aapl", "global", use_cache=False)
    assert result.ticker == "AAPL"
    assert result.market == "GLOBAL"


@pytest.mark.asyncio
async def test_dedupes_posts_across_sources(no_llm, monkeypatch):
    dup = _post("strong buy rally", pid="shared:1", source="reddit")
    dup_other_source = {**dup, "source": "stocktwits"}  # same id
    _stub_sources(monkeypatch, reddit=[dup], stocktwits=[dup_other_source])
    result = await get_sentiment("AAPL", "GLOBAL", use_cache=False)
    assert result.post_count == 1


# --------------------------------------------------------------------------- #
# Node wrapper
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sentiment_agent_node_wrapper(no_llm, monkeypatch):
    _stub_sources(monkeypatch, reddit=[BULL_1, BULL_2], stocktwits=[BEAR_1])
    state = {"ticker": "AAPL", "market": "GLOBAL", "use_cache": False, "other": "keep"}
    out = await sentiment_agent.sentiment_agent(state)

    assert out["other"] == "keep"  # original state preserved
    data = out["sentiment_data"]
    assert data["ticker"] == "AAPL"
    assert data["post_count"] == 3
    assert set(["overall_sentiment", "bullish_pct", "bearish_pct", "post_count"]).issubset(data)


# --------------------------------------------------------------------------- #
# Live (network) — deselected by default with -m "not live"
# --------------------------------------------------------------------------- #


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_stocktwits_returns_posts():
    from scrapers.stocktwits_sentiment import fetch_stocktwits_sentiment

    posts = await fetch_stocktwits_sentiment("AAPL")
    assert isinstance(posts, list)
    if posts:  # StockTwits occasionally rate-limits; tolerate empties
        assert posts[0]["source"] == "stocktwits"
        assert "text" in posts[0]
