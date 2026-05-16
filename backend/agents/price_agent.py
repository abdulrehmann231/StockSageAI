"""Price Agent.

Routes a ticker to the correct upstream data source (yfinance for
global, Playwright scrape for PSX), normalizes the response into
PriceQuote, and caches the result in Redis with a 60-second TTL.

Features:
- Dual-source routing (yfinance for global, PSX scraper for Pakistani stocks)
- Short-term cache (60s) for fresh quotes
- Long-term OHLC cache (24h) to fill in missing intraday data after market close
- Complete data extraction for both markets
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

# Cache prefixes and TTLs
CACHE_PREFIX = "price:"
CACHE_TTL_SECONDS = 60

# Long-term OHLC cache for after-hours data
OHLC_CACHE_PREFIX = "ohlc:"
OHLC_CACHE_TTL_SECONDS = 86400  # 24 hours


# ---------- Global (yfinance) ----------


def _safe_info(ticker_obj: yf.Ticker) -> dict[str, Any]:
    """`Ticker.info` periodically raises on Yahoo's anti-bot 401s."""
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


# ---------- OHLC Cache (for after-hours data) ----------


async def _get_cached_ohlc(ticker: str) -> dict[str, Any] | None:
    """Retrieve cached OHLC data from long-term cache."""
    cache_key = f"{OHLC_CACHE_PREFIX}{ticker}"
    return await cache_service.get_json(cache_key)


async def _save_ohlc_cache(ticker: str, data: dict[str, Any]) -> None:
    """Save OHLC data to long-term cache.

    Only saves if we have valid OHLC values (non-zero).
    """
    # Only cache if we have valid OHLC data
    if not data.get("open") or not data.get("day_high") or not data.get("day_low"):
        return

    ohlc_data = {
        "open": data.get("open"),
        "day_high": data.get("day_high"),
        "day_low": data.get("day_low"),
        "volume": data.get("volume"),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }

    cache_key = f"{OHLC_CACHE_PREFIX}{ticker}"
    await cache_service.set_json(cache_key, ohlc_data, ttl_seconds=OHLC_CACHE_TTL_SECONDS)
    logger.debug("[%s] Saved OHLC to long-term cache", ticker)


def _merge_ohlc_from_cache(raw: dict[str, Any], cached_ohlc: dict[str, Any]) -> dict[str, Any]:
    """Merge cached OHLC data into raw quote if current values are missing."""
    # Only fill in missing values
    if not raw.get("open") and cached_ohlc.get("open"):
        raw["open"] = cached_ohlc["open"]
        logger.debug("[%s] Using cached open: %.2f", raw["ticker"], raw["open"])

    if not raw.get("day_high") and cached_ohlc.get("day_high"):
        raw["day_high"] = cached_ohlc["day_high"]
        logger.debug("[%s] Using cached day_high: %.2f", raw["ticker"], raw["day_high"])

    if not raw.get("day_low") and cached_ohlc.get("day_low"):
        raw["day_low"] = cached_ohlc["day_low"]
        logger.debug("[%s] Using cached day_low: %.2f", raw["ticker"], raw["day_low"])

    if not raw.get("volume") and cached_ohlc.get("volume"):
        raw["volume"] = cached_ohlc["volume"]
        logger.debug("[%s] Using cached volume: %d", raw["ticker"], raw["volume"])

    return raw


# ---------- Public API ----------


async def get_price(
    ticker: str,
    market: str,
    *,
    use_cache: bool = True,
) -> PriceQuote:
    """Fetch a PriceQuote, hitting Redis first.

    For PSX stocks, if OHLC data is missing (after market hours),
    we fill in from a 24-hour cache of last known values.

    Raises ValueError if the upstream cannot return a price.
    """
    ticker = ticker.upper()
    cache_key = f"{CACHE_PREFIX}{ticker}"

    # Check short-term cache first
    if use_cache:
        cached = await cache_service.get_json(cache_key)
        if cached:
            cached["cached"] = True
            return PriceQuote.model_validate(cached)

    # Fetch fresh data
    if market == "PSX":
        raw = await _fetch_psx_quote(ticker)
        source = "psx"

        # Check if OHLC is missing and try to fill from long-term cache
        if not raw.get("open") or not raw.get("day_high") or not raw.get("day_low"):
            cached_ohlc = await _get_cached_ohlc(ticker)
            if cached_ohlc:
                logger.info("[%s] OHLC missing, using cached values from %s",
                           ticker, cached_ohlc.get("cached_at", "unknown"))
                raw = _merge_ohlc_from_cache(raw, cached_ohlc)

        # Save OHLC to long-term cache if we have valid data
        if raw.get("open") and raw.get("day_high") and raw.get("day_low"):
            await _save_ohlc_cache(ticker, raw)
    else:
        raw = await _fetch_global_quote(ticker, market)
        source = "yfinance"

    raw["fetched_at"] = datetime.now(timezone.utc)
    raw["source"] = source
    raw["cached"] = False

    quote = PriceQuote.model_validate(raw)

    # Save to short-term cache
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
