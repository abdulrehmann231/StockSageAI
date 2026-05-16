"""Playwright-based price scraper for the Pakistan Stock Exchange.

Targets dps.psx.com.pk/company/<TICKER>. The site is JS-rendered so we
launch headless Chromium, parse the visible quote block, then walk
``.stats_item`` rows by label.

Improvements over initial version:
- Fixed 52w range parsing for high-priced tickers (handles text fallback)
- Added market cap, EPS extraction from additional page sections
- Comprehensive logging for selector failures (early warning for redesigns)
- Better error messages with ticker context
- Browser pool support for better performance

Windows note: the project uses ``WindowsSelectorEventLoopPolicy`` for
psycopg-async, but Playwright's worker-thread loop needs subprocess
support. We swap to the Proactor policy just for the duration of the
scrape when not using the pool.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from contextlib import contextmanager
from typing import Any

from playwright.sync_api import Page, sync_playwright, TimeoutError as PlaywrightTimeout

PSX_URL = "https://dps.psx.com.pk/company/{ticker}"

logger = logging.getLogger(__name__)


@contextmanager
def _proactor_loop_policy_on_windows():
    if sys.platform != "win32":
        yield
        return
    prev = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(prev)


# Match numbers with optional thousands separators and decimals
# Order matters: try comma-separated first, then plain numbers
_FIRST_NUMBER_RE = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def _to_float(value: str | None) -> float | None:
    """Extract the first signed number from a string, ignoring currency
    prefixes (Rs.), thousands commas, and trailing units."""
    if value is None:
        return None
    match = _FIRST_NUMBER_RE.search(value)
    if not match:
        return None
    cleaned = match.group(0).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _parse_range_text(text: str) -> tuple[float | None, float | None]:
    """Parse a range like '152.17 — 369.99' or '6,402.02 — 10,524.97'.

    Returns (low, high) tuple.
    """
    if not text:
        return None, None
    # Split on various dash characters
    parts = re.split(r'[—–\-]', text)
    if len(parts) >= 2:
        low = _to_float(parts[0].strip())
        high = _to_float(parts[1].strip())
        return low, high
    return None, None


def _read_stats(page: Page, ticker: str) -> dict[str, str]:
    """Walk every ``.stats_item`` in the quote block.

    Returns a {label: value_text} map keyed on the lowercased label so
    callers can look up "open", "high", "p/e ratio (ttm)", etc.
    """
    items = page.locator(".quote__stats .stats_item")
    try:
        count = items.count()
    except Exception as exc:
        logger.warning(
            "[%s] Failed to count .stats_item elements: %s", ticker, exc
        )
        return {}

    out: dict[str, str] = {}
    for i in range(count):
        item = items.nth(i)
        try:
            label = item.locator(".stats_label").first.text_content(timeout=1_000) or ""
            value = item.locator(".stats_value").first.text_content(timeout=1_000) or ""
        except Exception as exc:
            logger.debug("[%s] Failed to read stats_item %d: %s", ticker, i, exc)
            continue
        out[label.strip().lower()] = value.strip()

    if not out:
        logger.warning(
            "[%s] No stats items found - PSX page structure may have changed", ticker
        )

    return out


def _read_52w_range(page: Page, ticker: str) -> tuple[float | None, float | None]:
    """52-week range - try data attributes first, fall back to text parsing.

    The data-low/data-high attributes on .numRange elements can be unreliable
    for high-priced tickers (values sometimes appear divided by 10 or 100).
    We now prefer parsing the visible text as it's always accurate.
    """
    items = page.locator(".quote__stats .stats_item")

    for i in range(items.count()):
        item = items.nth(i)
        try:
            label = (item.locator(".stats_label").first.text_content(timeout=500) or "").strip().lower()
        except Exception:
            continue

        if "52" in label and "week" in label and "range" in label:
            # First try: parse the visible text (most reliable)
            try:
                value_text = item.locator(".stats_value").first.text_content(timeout=500)
                if value_text:
                    low, high = _parse_range_text(value_text)
                    if low is not None and high is not None:
                        logger.debug(
                            "[%s] 52w range from text: low=%.2f, high=%.2f",
                            ticker, low, high
                        )
                        return low, high
            except Exception as exc:
                logger.debug("[%s] Failed to get 52w range text: %s", ticker, exc)

            # Fallback: try data attributes (may be inaccurate for high-priced stocks)
            try:
                numrange = item.locator(".numRange").first
                low_attr = numrange.get_attribute("data-low", timeout=500)
                high_attr = numrange.get_attribute("data-high", timeout=500)
                low = _to_float(low_attr)
                high = _to_float(high_attr)

                if low is not None and high is not None:
                    logger.debug(
                        "[%s] 52w range from data attrs: low=%.2f, high=%.2f",
                        ticker, low, high
                    )
                    return low, high
            except Exception as exc:
                logger.debug("[%s] Failed to get 52w range data attrs: %s", ticker, exc)

            return None, None

    logger.debug("[%s] 52-week range element not found", ticker)
    return None, None


def _read_market_cap(page: Page, ticker: str) -> float | None:
    """Extract market cap from the company info section.

    Market cap is displayed in thousands (e.g., "403,164,411.82" means ~403 billion PKR).
    We return it in raw thousands as displayed.
    """
    try:
        # Look for market cap in various possible locations
        selectors = [
            "text=Market Cap",
            "text=Market Capitalization",
            ".company-info:has-text('Market Cap')",
        ]

        for selector in selectors:
            try:
                elem = page.locator(selector).first
                if elem.count() > 0:
                    # Get the parent or sibling element containing the value
                    parent = elem.locator("..").first
                    text = parent.text_content(timeout=1_000)
                    if text:
                        # Extract the number after "Market Cap"
                        match = re.search(r'Market\s*Cap[:\s]*([0-9,]+\.?\d*)', text, re.I)
                        if match:
                            return _to_float(match.group(1))
            except Exception:
                continue

    except Exception as exc:
        logger.debug("[%s] Failed to extract market cap: %s", ticker, exc)

    return None


def _read_eps(page: Page, ticker: str) -> float | None:
    """Extract EPS from the page.

    PSX shows multiple EPS values (annual, quarterly). We try to get the
    most recent annual EPS first.
    """
    try:
        # Look for EPS in the financials or ratios section
        text = page.content()

        # Try to find annual EPS pattern
        patterns = [
            r'EPS[:\s]*Rs\.?\s*([0-9,]+\.?\d*)',
            r'Earnings\s*Per\s*Share[:\s]*([0-9,]+\.?\d*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                eps = _to_float(match.group(1))
                if eps is not None:
                    logger.debug("[%s] Found EPS: %.2f", ticker, eps)
                    return eps

    except Exception as exc:
        logger.debug("[%s] Failed to extract EPS: %s", ticker, exc)

    return None


def _scrape_page(page: Page, ticker: str, url: str, timeout_ms: int) -> dict[str, Any]:
    """Core scraping logic for a PSX stock page.

    Args:
        page: Playwright page object (from pool or fresh browser)
        ticker: Stock ticker symbol
        url: Full URL to scrape
        timeout_ms: Timeout for page operations

    Returns:
        Dictionary with price quote data

    Raises:
        ValueError: If price cannot be extracted
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.error("[%s] Timeout loading PSX page", ticker)
        raise ValueError(f"Timeout loading PSX page for {ticker}")

    # Wait for price element
    try:
        page.wait_for_selector(".quote__close", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.error(
            "[%s] .quote__close selector not found - page structure may have changed",
            ticker
        )
        raise ValueError(f"PSX page structure error for {ticker}: .quote__close not found")

    # Stats panel is rendered after initial paint
    try:
        page.wait_for_selector(".quote__stats .stats_item", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.warning(
            "[%s] .stats_item not found - continuing with limited data",
            ticker
        )

    # Extract price
    close_elem = page.locator(".quote__close").first
    close_txt = close_elem.text_content()

    price = _to_float(close_txt)
    if price is None or price == 0.0:
        logger.error("[%s] No valid price found in .quote__close: %r", ticker, close_txt)
        raise ValueError(f"PSX page returned no price for {ticker}")

    # Extract change
    change_txt = page.locator(".quote__change").first.text_content() or ""
    change_num: float | None = None
    change_pct: float | None = None

    stripped = change_txt.strip()
    if stripped:
        first_token = stripped.split()[0] if stripped.split() else None
        change_num = _to_float(first_token)
        pct_match = re.search(r"\(([-+]?\d+\.?\d*)\s*%\)", stripped)
        if pct_match:
            change_pct = float(pct_match.group(1))

        # Check for negative class
        change_class = page.locator(".quote__change").first.get_attribute("class") or ""
        if "neg" in change_class:
            if change_num is not None and change_num > 0:
                change_num = -change_num
            if change_pct is not None and change_pct > 0:
                change_pct = -change_pct

    # Read all stats
    stats = _read_stats(page, ticker)

    # 52-week range with improved parsing
    w52_low, w52_high = _read_52w_range(page, ticker)

    # Validate 52w range against current price
    if w52_low is not None and w52_high is not None:
        # Sanity check: 52w range should contain or be near current price
        margin = price * 0.5  # 50% margin for validation
        if w52_high < price - margin or w52_low > price + margin:
            logger.warning(
                "[%s] 52w range (%.2f - %.2f) seems inconsistent with price %.2f",
                ticker, w52_low, w52_high, price
            )

    # Previous close - prefer change-implied for consistency
    if change_num is not None:
        previous_close = price - change_num
    else:
        previous_close = _to_float(stats.get("ldcp"))

    # P/E ratio
    pe_ratio = _to_float(stats.get("p/e ratio (ttm)"))
    if pe_ratio is None:
        pe_ratio = _to_float(stats.get("p/e ratio"))
    if pe_ratio is None:
        pe_ratio = _to_float(stats.get("pe ratio"))

    # Try to get market cap from page (not always in stats section)
    market_cap = _read_market_cap(page, ticker)

    # Try to get EPS
    eps = _read_eps(page, ticker)

    result = {
        "ticker": ticker.upper(),
        "market": "PSX",
        "currency": "PKR",
        "price": price,
        "previous_close": previous_close,
        "open": _to_float(stats.get("open")),
        "day_high": _to_float(stats.get("high")),
        "day_low": _to_float(stats.get("low")),
        "volume": _to_int(stats.get("volume")),
        "week_52_high": w52_high,
        "week_52_low": w52_low,
        "market_cap": market_cap,
        "pe_ratio": pe_ratio,
        "eps": eps,
        "dividend_yield": None,  # Would need to scrape Payouts section
        "change": change_num,
        "change_pct": change_pct,
    }

    logger.debug(
        "[%s] Successfully scraped: price=%.2f, change=%.2f%%, 52w=[%.2f-%.2f]",
        ticker,
        price,
        change_pct or 0,
        w52_low or 0,
        w52_high or 0
    )

    return result


def _fetch_sync(ticker: str, timeout_ms: int, *, use_pool: bool = True) -> dict[str, Any]:
    """Synchronous fetch that can use browser pool or fresh browser.

    Args:
        ticker: Stock ticker symbol
        timeout_ms: Timeout for page operations
        use_pool: If True, use browser pool for better performance.
                  If False, launch a fresh browser (useful for testing).

    Returns:
        Dictionary with price quote data
    """
    url = PSX_URL.format(ticker=ticker.upper())
    logger.debug("[%s] Fetching PSX quote from %s (pool=%s)", ticker, url, use_pool)

    if use_pool:
        # Use browser pool for better performance
        from scrapers.browser_pool import get_browser_pool

        pool = get_browser_pool()
        with pool.get_page_sync() as page:
            return _scrape_page(page, ticker, url, timeout_ms)
    else:
        # Fallback: launch new browser (for testing or isolation)
        with _proactor_loop_policy_on_windows(), sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"
                    )
                )
                page = context.new_page()
                return _scrape_page(page, ticker, url, timeout_ms)
            finally:
                browser.close()


async def fetch_psx_quote(
    ticker: str,
    *,
    timeout_ms: int = 30_000,
    use_pool: bool = True,
) -> dict[str, Any]:
    """Async wrapper that offloads the sync Playwright run to a thread.

    Args:
        ticker: Stock ticker symbol
        timeout_ms: Timeout for page operations
        use_pool: If True, use browser pool for better performance

    Returns:
        Dictionary with price quote data
    """
    return await asyncio.to_thread(_fetch_sync, ticker, timeout_ms, use_pool=use_pool)
