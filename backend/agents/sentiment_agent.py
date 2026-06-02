"""Sentiment Agent.

Gauges public / community sentiment for a ticker by gathering recent social
posts (Reddit + StockTwits for global; Reddit for PSX) and condensing them into
a single bullish/bearish read for the report pipeline.

Flow:
1. Fan out to per-source fetchers (each isolated so one failure can't sink the
   run) and collect normalized posts.
2. Try an LLM pass that scores overall sentiment and extracts the top bullish /
   bearish talking points.
3. If the LLM is unavailable or returns something unusable, fall back to a
   deterministic keyword + provider-label scorer so the agent always returns a
   well-formed result.
4. Cache the result in Redis (2h TTL) keyed by ticker.

Mirrors the conventions of ``agents/news_agent.py``: optional API keys, per-source
error isolation, Redis failures never block a fresh fetch, and a LangGraph-style
``sentiment_agent(state)`` node wrapper plus a small CLI for local testing.

Return shape (see plan § 4.7)::

    {
      "overall_sentiment": 0.34,   # float in [-1, 1]
      "bullish_pct": 67,
      "bearish_pct": 33,
      "top_bullish_points": [...],
      "top_bearish_points": [...],
      "post_count": 142
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env", override=False)

from services import cache_service  # noqa: E402
from services import llm_service  # noqa: E402
from scrapers.reddit_sentiment import fetch_reddit_sentiment  # noqa: E402
from scrapers.stocktwits_sentiment import fetch_stocktwits_sentiment  # noqa: E402

CACHE_PREFIX = "sentiment:"
CACHE_TTL_SECONDS = 2 * 60 * 60  # 2 hours
MAX_POSTS_FOR_LLM = 60
MAX_TEXT_CHARS = 280
DEFAULT_POINTS = 3
# Above this absolute mean polarity we call the crowd bullish / bearish rather
# than neutral.
SENTIMENT_LABEL_THRESHOLD = 0.15


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class SentimentResult(BaseModel):
    ticker: str
    market: str
    company_name: str | None = None
    overall_sentiment: float = 0.0  # -1 (very bearish) .. +1 (very bullish)
    label: str = "neutral"  # bullish | neutral | bearish (bucketed sentiment)
    bullish_pct: int = 0
    bearish_pct: int = 0
    top_bullish_points: list[str] = Field(default_factory=list)
    top_bearish_points: list[str] = Field(default_factory=list)
    post_count: int = 0
    sources: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    fetched_at: datetime
    cached: bool = False


# --------------------------------------------------------------------------- #
# Keyword lexicon (deterministic fallback)
# --------------------------------------------------------------------------- #

BULLISH_TERMS = {
    "buy", "buying", "bought", "long", "calls", "moon", "mooning", "bullish",
    "undervalued", "breakout", "rally", "rallying", "beat", "beats", "strong",
    "upgrade", "upgraded", "growth", "accumulate", "accumulating", "pump",
    "gains", "rocket", "rockets", "surge", "surging", "outperform", "buy the dip",
    "all-time high", "ath", "uptrend", "support", "oversold", "🚀", "📈", "💎",
}

BEARISH_TERMS = {
    "sell", "selling", "sold", "short", "shorting", "puts", "bearish",
    "overvalued", "crash", "crashing", "dump", "dumping", "miss", "missed",
    "weak", "downgrade", "downgraded", "debt", "bankruptcy", "bankrupt", "loss",
    "losses", "lawsuit", "fraud", "drop", "dropping", "fall", "falling",
    "decline", "declining", "bagholder", "rug", "rugged", "downtrend",
    "resistance", "overbought", "tank", "tanking", "📉", "🩸",
}

_WORD_RE = re.compile(r"[a-z0-9\-']+")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def classify_post(text: str, provider_label: str | None = None) -> str:
    """Classify a single post as 'bullish' | 'bearish' | 'neutral'.

    A provider-supplied label (e.g. StockTwits' Bullish/Bearish tag) wins; we
    only fall back to keyword counting when no label is present.
    """
    if provider_label in ("bullish", "bearish"):
        return provider_label

    lowered = text.lower()
    tokens = set(_tokenize(text))

    bull = sum(1 for term in BULLISH_TERMS if (term in tokens or term in lowered))
    bear = sum(1 for term in BEARISH_TERMS if (term in tokens or term in lowered))

    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _bucket_label(score: float) -> str:
    if score >= SENTIMENT_LABEL_THRESHOLD:
        return "bullish"
    if score <= -SENTIMENT_LABEL_THRESHOLD:
        return "bearish"
    return "neutral"


def _clean_snippet(text: str, limit: int = 160) -> str:
    snippet = re.sub(r"\s+", " ", text).strip()
    if len(snippet) > limit:
        snippet = snippet[: limit - 1].rstrip() + "…"
    return snippet


# --------------------------------------------------------------------------- #
# Deterministic scoring
# --------------------------------------------------------------------------- #


def _deterministic_score(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Keyword + provider-label scorer used when the LLM is unavailable."""
    bullish_posts: list[dict[str, Any]] = []
    bearish_posts: list[dict[str, Any]] = []

    for post in posts:
        verdict = classify_post(post.get("text", ""), post.get("label"))
        if verdict == "bullish":
            bullish_posts.append(post)
        elif verdict == "bearish":
            bearish_posts.append(post)

    bull = len(bullish_posts)
    bear = len(bearish_posts)
    directional = bull + bear

    if directional == 0:
        overall = 0.0
        bullish_pct = 0
        bearish_pct = 0
    else:
        overall = round((bull - bear) / directional, 3)
        bullish_pct = round(bull / directional * 100)
        bearish_pct = 100 - bullish_pct

    def _top_points(rows: list[dict[str, Any]]) -> list[str]:
        ranked = sorted(rows, key=lambda p: (p.get("score") or 0), reverse=True)
        points: list[str] = []
        seen: set[str] = set()
        for row in ranked:
            snippet = _clean_snippet(row.get("text", ""))
            key = snippet.lower()
            if snippet and key not in seen:
                seen.add(key)
                points.append(snippet)
            if len(points) >= DEFAULT_POINTS:
                break
        return points

    return {
        "overall_sentiment": overall,
        "bullish_pct": bullish_pct,
        "bearish_pct": bearish_pct,
        "top_bullish_points": _top_points(bullish_posts),
        "top_bearish_points": _top_points(bearish_posts),
    }


def _coerce_llm_scores(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and normalize an LLM scoring payload; None if unusable."""
    if not isinstance(raw, dict):
        return None
    try:
        overall = float(raw.get("overall_sentiment"))
    except (TypeError, ValueError):
        return None
    if overall != overall:  # NaN guard
        return None
    overall = max(-1.0, min(1.0, round(overall, 3)))

    def _pct(value: Any) -> int | None:
        try:
            return max(0, min(100, int(round(float(value)))))
        except (TypeError, ValueError):
            return None

    bullish_pct = _pct(raw.get("bullish_pct"))
    bearish_pct = _pct(raw.get("bearish_pct"))
    if bullish_pct is None and bearish_pct is None:
        return None
    if bullish_pct is None:
        bullish_pct = max(0, 100 - (bearish_pct or 0))
    if bearish_pct is None:
        bearish_pct = max(0, 100 - bullish_pct)
    # Renormalize so the two halves sum to 100 (LLMs sometimes drift).
    total = bullish_pct + bearish_pct
    if total > 0 and total != 100:
        bullish_pct = round(bullish_pct / total * 100)
        bearish_pct = 100 - bullish_pct

    def _points(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(_clean_snippet(item, limit=200))
            if len(out) >= DEFAULT_POINTS:
                break
        return out

    return {
        "overall_sentiment": overall,
        "bullish_pct": bullish_pct,
        "bearish_pct": bearish_pct,
        "top_bullish_points": _points(raw.get("top_bullish_points")),
        "top_bearish_points": _points(raw.get("top_bearish_points")),
    }


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def _source_fetchers(market: str) -> list[tuple[str, Any, tuple]]:
    """Return (name, coroutine-fn, args) tuples for the market's sources."""
    if market == "PSX":
        # StockTwits rarely covers PSX symbols; Reddit (PakistaniInvestors) is
        # the practical source today. Telegram/X scraping is a future add-on.
        return [
            ("reddit", fetch_reddit_sentiment, ("PSX",)),
        ]
    return [
        ("reddit", fetch_reddit_sentiment, ("GLOBAL",)),
        ("stocktwits", fetch_stocktwits_sentiment, ("GLOBAL",)),
    ]


async def _gather_posts(
    ticker: str,
    market: str,
    company_name: str | None,
    errors: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    fetchers = _source_fetchers(market)
    coros = [
        fn(ticker, market_arg, company_name)
        for _name, fn, (market_arg,) in fetchers
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    posts: list[dict[str, Any]] = []
    used_sources: list[str] = []
    for (name, _fn, _args), result in zip(fetchers, results):
        if isinstance(result, Exception):
            msg = f"{name}: {result}" or repr(result)
            errors.append(msg)
            logger.info("Sentiment source %s failed for %s: %s", name, ticker, result)
            continue
        if result:
            used_sources.append(name)
            posts.extend(result)

    return _dedupe_posts(posts), used_sources


def _dedupe_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for post in posts:
        text = (post.get("text") or "").strip()
        if not text:
            continue
        key = post.get("id") or _clean_snippet(text, limit=120).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(post)
    return out


def _posts_for_llm(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for post in posts[:MAX_POSTS_FOR_LLM]:
        text = (post.get("text") or "")[:MAX_TEXT_CHARS]
        payload.append(
            {
                "source": post.get("source"),
                "text": text,
                "label": post.get("label"),
            }
        )
    return payload


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


async def get_sentiment(
    ticker: str,
    market: str = "GLOBAL",
    *,
    company_name: str | None = None,
    use_cache: bool = True,
) -> SentimentResult:
    """Fetch and score community sentiment for ``ticker``.

    Always returns a well-formed :class:`SentimentResult`; on total source
    failure it returns a neutral result with the errors recorded.
    """
    ticker = ticker.upper()
    market = (market or "GLOBAL").upper()
    cache_key = f"{CACHE_PREFIX}{market}:{ticker}"

    if use_cache:
        try:
            cached = await cache_service.get_json(cache_key)
        except Exception as exc:  # noqa: BLE001
            logger.info("Sentiment cache read failed for %s: %s", ticker, exc)
            cached = None
        if cached:
            cached["cached"] = True
            return SentimentResult.model_validate(cached)

    errors: list[str] = []
    posts, used_sources = await _gather_posts(ticker, market, company_name, errors)

    if not posts:
        result = SentimentResult(
            ticker=ticker,
            market=market,
            company_name=company_name,
            overall_sentiment=0.0,
            label="neutral",
            bullish_pct=0,
            bearish_pct=0,
            post_count=0,
            sources=used_sources,
            errors=errors,
            fetched_at=datetime.now(timezone.utc),
            cached=False,
        )
        await _maybe_cache(cache_key, result, use_cache)
        return result

    scores = await _score_posts(ticker, market, company_name, posts, errors)

    result = SentimentResult(
        ticker=ticker,
        market=market,
        company_name=company_name,
        overall_sentiment=scores["overall_sentiment"],
        label=_bucket_label(scores["overall_sentiment"]),
        bullish_pct=scores["bullish_pct"],
        bearish_pct=scores["bearish_pct"],
        top_bullish_points=scores["top_bullish_points"],
        top_bearish_points=scores["top_bearish_points"],
        post_count=len(posts),
        sources=used_sources,
        errors=errors,
        fetched_at=datetime.now(timezone.utc),
        cached=False,
    )
    await _maybe_cache(cache_key, result, use_cache)
    return result


async def _score_posts(
    ticker: str,
    market: str,
    company_name: str | None,
    posts: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    """Try the LLM scorer, validate it, and fall back to deterministic rules."""
    try:
        llm_raw = await llm_service.analyze_sentiment_posts(
            ticker=ticker,
            market=market,
            company_name=company_name,
            posts=_posts_for_llm(posts),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Sentiment LLM call raised for %s: %s", ticker, exc)
        llm_raw = None

    if llm_raw is not None:
        coerced = _coerce_llm_scores(llm_raw)
        if coerced is not None:
            # If the LLM declined to extract points, backfill from the posts so
            # the report always has something concrete to show.
            fallback = _deterministic_score(posts)
            if not coerced["top_bullish_points"]:
                coerced["top_bullish_points"] = fallback["top_bullish_points"]
            if not coerced["top_bearish_points"]:
                coerced["top_bearish_points"] = fallback["top_bearish_points"]
            return coerced
        errors.append("llm: returned unusable sentiment payload")

    return _deterministic_score(posts)


async def _maybe_cache(cache_key: str, result: SentimentResult, use_cache: bool) -> None:
    if not use_cache:
        return
    try:
        await cache_service.set_json(
            cache_key, result.model_dump(mode="json"), ttl_seconds=CACHE_TTL_SECONDS
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Sentiment cache write failed for %s: %s", result.ticker, exc)


async def sentiment_agent(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph-compatible node wrapper.

    Expects ``ticker`` and ``market`` in state. Optional keys: ``company_name``
    and ``use_cache``. Returns a shallow copy of state with ``sentiment_data``
    populated.
    """
    result = await get_sentiment(
        ticker=str(state["ticker"]),
        market=str(state.get("market", "GLOBAL")),
        company_name=state.get("company_name"),
        use_cache=bool(state.get("use_cache", True)),
    )
    return {**state, "sentiment_data": result.model_dump(mode="json")}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


async def _lookup_stock(ticker: str) -> dict[str, str] | None:
    try:
        from sqlalchemy import select

        from db.models import Stock
        from db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        logger.debug("Unable to import DB lookup dependencies: %s", exc)
        return None

    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(Stock).where(Stock.ticker == ticker.upper())
            )
            stock = result.scalar_one_or_none()
            if stock is None:
                return None
            return {"market": stock.market, "name": stock.name}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Ticker lookup failed for %s: %s", ticker, exc)
        return None


async def _cli() -> None:
    parser = argparse.ArgumentParser(description="Run the StockSage Sentiment Agent.")
    parser.add_argument("ticker", help="Ticker symbol")
    parser.add_argument(
        "market",
        nargs="?",
        help="Optional market override, e.g. NASDAQ, NYSE, GLOBAL, or PSX",
    )
    parser.add_argument("--company", dest="company_name", default=None)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    market = args.market
    company_name = args.company_name
    stock = await _lookup_stock(args.ticker)
    if market is None:
        market = stock["market"] if stock else "GLOBAL"
    if stock is not None:
        company_name = company_name or stock["name"]

    result = await get_sentiment(
        args.ticker,
        market,
        company_name=company_name,
        use_cache=not args.no_cache,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_cli())
