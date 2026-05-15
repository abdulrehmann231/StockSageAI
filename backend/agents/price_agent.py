"""Price Agent.

Routes a ticker to the correct upstream data source (yfinance for
global, Playwright scrape for PSX), normalizes the response into
PriceQuote, and caches the result in Redis with a 60-second TTL.

The agent intentionally exposes the fetch path as two private helpers
(`_fetch_global_quote`, `_fetch_psx_quote`) so tests can monkey-patch
them without going through the network or spinning up a real browser.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

from db.schemas import PriceQuote
from scrapers.psx_prices import fetch_psx_quote
from services import cache_service

logger = logging.getLogger(__name__)

CACHE_PREFIX = "price:"
CACHE_TTL_SECONDS = 60


# ---------- Global (yfinance) ----------


def _safe_info(ticker_obj: yf.Ticker) -> dict[str, Any]:
    """`Ticker.info` periodically raises on Yahoo's anti-bot 401s.

    Treat it as best-effort metadata rather than the source of truth.
    """
    try:
        return ticker_obj.info or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance .info failed for %s: %s", ticker_obj.ticker, exc)
        return {}


def _safe_fast_info(ticker_obj: yf.Ticker) -> dict[str, Any]:
    """fast_info is a thin wrapper over the chart endpoint — more reliable."""
    try:
        fast = ticker_obj.fast_info
        return {k: fast[k] for k in fast.keys()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance .fast_info failed for %s: %s", ticker_obj.ticker, exc)
        return {}


def _fetch_global_quote_sync(ticker: str, market: str) -> dict[str, Any]:
    t = yf.Ticker(ticker)

    # 1. History — the most reliable Yahoo endpoint. Need 2d so we can
    #    compute previous_close from yesterday's close.
    hist = t.history(period="5d", auto_adjust=False)
    if hist.empty:
        raise ValueError(f"No price history for {ticker}")

    latest = hist.iloc[-1]
    price = float(latest["Close"])
    open_p = float(latest["Open"])
    high = float(latest["High"])
    low = float(latest["Low"])
    volume = int(latest["Volume"]) if latest["Volume"] == latest["Volume"] else None

    previous_close: float | None = None
    if len(hist) >= 2:
        previous_close = float(hist.iloc[-2]["Close"])

    fast = _safe_fast_info(t)
    info = _safe_info(t)

    if previous_close is None:
        previous_close = _maybe_float(
            fast.get("previousClose") or info.get("previousClose")
        )

    change = None
    change_pct = None
    if previous_close is not None and previous_close != 0:
        change = price - previous_close
        change_pct = change / previous_close * 100.0

    currency = fast.get("currency") or info.get("currency") or "USD"
    week_52_high = _maybe_float(
        fast.get("yearHigh")
        or fast.get("fifty_two_week_high")
        or info.get("fiftyTwoWeekHigh")
    )
    week_52_low = _maybe_float(
        fast.get("yearLow")
        or fast.get("fifty_two_week_low")
        or info.get("fiftyTwoWeekLow")
    )
    market_cap = _maybe_float(fast.get("marketCap") or info.get("marketCap"))

    return {
        "ticker": ticker.upper(),
        "market": market,
        "currency": currency,
        "price": price,
        "previous_close": previous_close,
        "open": open_p,
        "day_high": high,
        "day_low": low,
        "volume": volume,
        "week_52_high": week_52_high,
        "week_52_low": week_52_low,
        "market_cap": market_cap,
        "pe_ratio": _maybe_float(info.get("trailingPE") or info.get("forwardPE")),
        "eps": _maybe_float(info.get("trailingEps") or info.get("forwardEps")),
        "dividend_yield": _maybe_float(info.get("dividendYield")),
        "change": change,
        "change_pct": change_pct,
    }


async def _fetch_global_quote(ticker: str, market: str) -> dict[str, Any]:
    return await asyncio.to_thread(_fetch_global_quote_sync, ticker, market)


# ---------- PSX (Playwright) ----------


async def _fetch_psx_quote(ticker: str) -> dict[str, Any]:
    return await fetch_psx_quote(ticker)


# ---------- Public API ----------


async def get_price(
    ticker: str,
    market: str,
    *,
    use_cache: bool = True,
) -> PriceQuote:
    """Fetch a PriceQuote, hitting Redis first.

    Raises ValueError if the upstream cannot return a price.
    """
    ticker = ticker.upper()
    cache_key = f"{CACHE_PREFIX}{ticker}"

    if use_cache:
        cached = await cache_service.get_json(cache_key)
        if cached:
            cached["cached"] = True
            return PriceQuote.model_validate(cached)

    if market == "PSX":
        raw = await _fetch_psx_quote(ticker)
        source = "psx"
    else:
        raw = await _fetch_global_quote(ticker, market)
        source = "yfinance"

    raw["fetched_at"] = datetime.now(timezone.utc)
    raw["source"] = source
    raw["cached"] = False

    quote = PriceQuote.model_validate(raw)
    await cache_service.set_json(
        cache_key,
        quote.model_dump(mode="json"),
        ttl_seconds=CACHE_TTL_SECONDS,
    )
    return quote


# ---------- helpers ----------


def _maybe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
