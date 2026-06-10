"""LangGraph-style orchestrator for Phase 5.

The plan calls for a multi-agent fan-out / fan-in: Price, News, and Sentiment
each run in parallel from a shared state, then a Report Writer node merges the
outputs into a single ``StockReport``.

We deliberately implement the fan-out using ``asyncio.gather`` instead of a
full LangGraph ``StateGraph``. The graph here has exactly one fan-out and one
fan-in, no conditional edges, no loops; pulling in LangGraph for that adds
runtime cost and makes testing harder while changing nothing observable about
the result. The orchestrator still exposes a LangGraph-compatible
``report_orchestrator(state)`` node wrapper so the existing
``price_agent(state)`` / ``news_agent(state)`` / ``sentiment_agent(state)``
contracts compose unchanged when we later want to drop the orchestrator into a
larger graph.

Behaviour:

- Each agent runs in its own task. A single agent failing does not sink the
  report — the failure is recorded in ``StockReport.errors`` and the
  corresponding payload is set to ``None``. The report writer is happy with any
  combination of present / absent inputs.
- Results are cached in Redis for 30 minutes keyed by ``report:{market}:{ticker}``.
- ``use_cache=False`` (and the API's ``?refresh=true``) bypass the cache for a
  fresh fan-out. Cache failures never block a fresh run.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from agents import news_agent, price_agent, sentiment_agent
from agents.news_agent import NewsResult
from agents.report_writer import StockReport, write_report
from agents.sentiment_agent import SentimentResult
from db.schemas import PriceQuote
from services import cache_service

logger = logging.getLogger(__name__)

CACHE_PREFIX = "report:"
CACHE_TTL_SECONDS = 30 * 60  # 30 minutes


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #


async def get_report(
    ticker: str,
    market: str,
    *,
    company_name: str | None = None,
    use_cache: bool = True,
    max_news_articles: int = 5,
) -> StockReport:
    """Run the three signal agents in parallel and synthesize a ``StockReport``."""
    ticker = ticker.strip().upper()
    market = (market or "GLOBAL").strip().upper()
    cache_key = f"{CACHE_PREFIX}{market}:{ticker}"

    if use_cache:
        cached = await _safe_cache_get(cache_key)
        if cached:
            try:
                cached_report = StockReport.model_validate(cached)
                cached_report.cached = True
                return cached_report
            except Exception as exc:  # noqa: BLE001
                logger.info("Cached report %s failed validation: %s", cache_key, exc)

    price, news, sentiment, errors = await _run_agents(
        ticker=ticker,
        market=market,
        company_name=company_name,
        use_cache=use_cache,
        max_news_articles=max_news_articles,
    )

    report = await write_report(
        ticker=ticker,
        market=market,
        company_name=company_name,
        price=price,
        news=news,
        sentiment=sentiment,
        errors=errors,
    )

    if use_cache:
        await _safe_cache_set(cache_key, report)

    return report


async def report_orchestrator(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph-compatible node wrapper.

    Expects ``ticker`` and ``market`` in state. Optional keys: ``company_name``,
    ``use_cache``, ``max_news_articles``. Returns a shallow copy of state with
    ``report_data`` populated (and ``price_data`` / ``news_data`` /
    ``sentiment_data`` for downstream graph nodes that want the raw payloads).
    """
    report = await get_report(
        ticker=str(state["ticker"]),
        market=str(state.get("market", "GLOBAL")),
        company_name=state.get("company_name"),
        use_cache=bool(state.get("use_cache", True)),
        max_news_articles=int(state.get("max_news_articles", 5)),
    )
    next_state = {**state, "report_data": report.model_dump(mode="json")}
    if report.price is not None:
        next_state["price_data"] = report.price.model_dump(mode="json")
    if report.news is not None:
        next_state["news_data"] = report.news.model_dump(mode="json")
    if report.sentiment is not None:
        next_state["sentiment_data"] = report.sentiment.model_dump(mode="json")
    return next_state


# --------------------------------------------------------------------------- #
# Fan-out execution
# --------------------------------------------------------------------------- #


async def _run_agents(
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    use_cache: bool,
    max_news_articles: int,
) -> tuple[PriceQuote | None, NewsResult | None, SentimentResult | None, list[str]]:
    """Run Price / News / Sentiment concurrently with per-task error isolation."""

    async def _run_price() -> PriceQuote:
        return await price_agent.get_price(ticker, market, use_cache=use_cache)

    async def _run_news() -> NewsResult:
        return await news_agent.get_news(
            ticker,
            market,
            company_name=company_name,
            max_articles=max_news_articles,
            use_cache=use_cache,
        )

    async def _run_sentiment() -> SentimentResult:
        return await sentiment_agent.get_sentiment(
            ticker,
            market,
            company_name=company_name,
            use_cache=use_cache,
        )

    started_at = datetime.now(timezone.utc)
    raw = await asyncio.gather(
        _run_price(),
        _run_news(),
        _run_sentiment(),
        return_exceptions=True,
    )
    elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    logger.info(
        "Orchestrator fan-out for %s/%s completed in %dms",
        market,
        ticker,
        elapsed_ms,
    )

    errors: list[str] = []
    price: PriceQuote | None = _coerce_result(raw[0], "price", errors, expected=PriceQuote)
    news: NewsResult | None = _coerce_result(raw[1], "news", errors, expected=NewsResult)
    sentiment: SentimentResult | None = _coerce_result(
        raw[2], "sentiment", errors, expected=SentimentResult
    )

    return price, news, sentiment, errors


def _coerce_result(
    value: Any,
    label: str,
    errors: list[str],
    *,
    expected: type,
) -> Any:
    """Validate one task result; record + null-out failures rather than raise."""
    if isinstance(value, BaseException):
        message = f"{label}: {value.__class__.__name__}: {value}".strip()
        errors.append(message)
        logger.info("Orchestrator agent failed (%s): %s", label, value)
        return None
    if not isinstance(value, expected):
        errors.append(f"{label}: unexpected payload type {type(value).__name__}")
        return None
    return value


# --------------------------------------------------------------------------- #
# Cache helpers
# --------------------------------------------------------------------------- #


async def _safe_cache_get(key: str) -> dict[str, Any] | None:
    try:
        return await cache_service.get_json(key)
    except Exception as exc:  # noqa: BLE001
        logger.info("Report cache read failed for %s: %s", key, exc)
        return None


async def _safe_cache_set(key: str, report: StockReport) -> None:
    try:
        # Stamp cached=False at write time; the read path flips it on hit so a
        # client can tell the response served from cache.
        payload = report.model_copy(update={"cached": False}).model_dump(mode="json")
        await cache_service.set_json(key, payload, ttl_seconds=CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.info("Report cache write failed for %s: %s", key, exc)
