"""Playwright-based price scraper for the Pakistan Stock Exchange.

Targets dps.psx.com.pk/company/<TICKER>. The site is JS-rendered so we
launch headless Chromium, parse the visible quote block, and extract
data from multiple sections (Quote, Equity, Financials, Ratios).

Features:
- Complete data extraction from all available page sections
- Fixed 52w range parsing for high-priced tickers
- Market cap, EPS, P/E ratio extraction from Equity/Financials sections
- Comprehensive logging for selector failures
- Browser pool support for better performance
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
_FIRST_NUMBER_RE = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def _to_float(value: str | None) -> float | None:
    """Extract the first signed number from a string."""
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
    """Parse a range like '152.17 — 369.99' or '6,402.02 — 10,524.97'."""
    if not text:
        return None, None
    parts = re.split(r'[—–\-]', text)
    if len(parts) >= 2:
        low = _to_float(parts[0].strip())
        high = _to_float(parts[1].strip())
        return low, high
    return None, None


def _read_stats(page: Page, ticker: str) -> dict[str, str]:
    """Walk every .stats_item in the quote block."""
    items = page.locator(".quote__stats .stats_item")
    try:
        count = items.count()
    except Exception as exc:
        logger.warning("[%s] Failed to count .stats_item elements: %s", ticker, exc)
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
        logger.warning("[%s] No stats items found - PSX page structure may have changed", ticker)

    return out


def _read_52w_range(page: Page, ticker: str) -> tuple[float | None, float | None]:
    """52-week range - parse visible text (most reliable)."""
    items = page.locator(".quote__stats .stats_item")

    for i in range(items.count()):
        item = items.nth(i)
        try:
            label = (item.locator(".stats_label").first.text_content(timeout=500) or "").strip().lower()
        except Exception:
            continue

        if "52" in label and "week" in label and "range" in label:
            try:
                value_text = item.locator(".stats_value").first.text_content(timeout=500)
                if value_text:
                    low, high = _parse_range_text(value_text)
                    if low is not None and high is not None:
                        logger.debug("[%s] 52w range: low=%.2f, high=%.2f", ticker, low, high)
                        return low, high
            except Exception as exc:
                logger.debug("[%s] Failed to get 52w range text: %s", ticker, exc)
            return None, None

    logger.debug("[%s] 52-week range element not found", ticker)
    return None, None


def _extract_equity_data(page: Page, ticker: str) -> dict[str, Any]:
    """Extract data from the Equity section (market cap, shares, free float)."""
    data: dict[str, Any] = {
        "market_cap": None,
        "total_shares": None,
        "free_float_shares": None,
        "free_float_pct": None,
    }

    try:
        # The equity section contains market cap and share information
        # Look for specific patterns in the page content
        page_text = page.content()

        # Market Cap - usually in format "Market Cap: 403,164,411.82"
        market_cap_patterns = [
            r'Market\s*Cap[:\s]*(?:Rs\.?\s*)?([0-9,]+\.?\d*)',
            r'Market\s*Capitalization[:\s]*([0-9,]+\.?\d*)',
        ]
        for pattern in market_cap_patterns:
            match = re.search(pattern, page_text, re.I)
            if match:
                data["market_cap"] = _to_float(match.group(1))
                if data["market_cap"]:
                    # PSX shows market cap in thousands, convert to actual value
                    data["market_cap"] = data["market_cap"] * 1000
                    logger.debug("[%s] Market cap: %.0f", ticker, data["market_cap"])
                    break

        # Total Shares
        shares_patterns = [
            r'Total\s*Shares[:\s]*([0-9,]+)',
            r'Shares\s*Outstanding[:\s]*([0-9,]+)',
        ]
        for pattern in shares_patterns:
            match = re.search(pattern, page_text, re.I)
            if match:
                data["total_shares"] = _to_int(match.group(1))
                break

        # Free Float
        float_pattern = r'Free\s*Float[:\s]*([0-9,]+).*?(\d+\.?\d*)\s*%'
        match = re.search(float_pattern, page_text, re.I | re.DOTALL)
        if match:
            data["free_float_shares"] = _to_int(match.group(1))
            data["free_float_pct"] = _to_float(match.group(2))

    except Exception as exc:
        logger.debug("[%s] Failed to extract equity data: %s", ticker, exc)

    return data


def _extract_financials_data(page: Page, ticker: str) -> dict[str, Any]:
    """Extract data from the Financials section (EPS, revenue, profit)."""
    data: dict[str, Any] = {
        "eps": None,
        "eps_quarterly": None,
        "annual_revenue": None,
        "annual_profit": None,
        "net_profit_margin": None,
    }

    try:
        page_text = page.content()

        # EPS - Look for annual EPS values
        # Pattern matches "EPS: 32.26" or "EPS 42.60" etc.
        eps_patterns = [
            r'EPS[:\s]*(?:Rs\.?\s*)?(\d+\.?\d*)',
            r'Earnings\s*Per\s*Share[:\s]*(\d+\.?\d*)',
        ]
        for pattern in eps_patterns:
            matches = re.findall(pattern, page_text, re.I)
            if matches:
                # Take the first (usually most recent) EPS value
                eps_val = _to_float(matches[0])
                if eps_val and eps_val > 0:
                    data["eps"] = eps_val
                    logger.debug("[%s] EPS: %.2f", ticker, eps_val)
                    break

        # Try to get quarterly EPS separately
        quarterly_pattern = r'Q[1-4]\s*\d{4}.*?EPS[:\s]*(\d+\.?\d*)'
        match = re.search(quarterly_pattern, page_text, re.I)
        if match:
            data["eps_quarterly"] = _to_float(match.group(1))

        # Profit After Tax
        profit_patterns = [
            r'Profit\s*After\s*Tax[:\s]*([0-9,]+)',
            r'Net\s*Profit[:\s]*([0-9,]+)',
        ]
        for pattern in profit_patterns:
            match = re.search(pattern, page_text, re.I)
            if match:
                data["annual_profit"] = _to_float(match.group(1))
                break

        # Net Profit Margin
        margin_pattern = r'Net\s*Profit\s*Margin[:\s]*(\d+\.?\d*)\s*%'
        match = re.search(margin_pattern, page_text, re.I)
        if match:
            data["net_profit_margin"] = _to_float(match.group(1))

    except Exception as exc:
        logger.debug("[%s] Failed to extract financials data: %s", ticker, exc)

    return data


def _extract_dividend_data(page: Page, ticker: str) -> dict[str, Any]:
    """Extract dividend information."""
    data: dict[str, Any] = {
        "dividend_yield": None,
        "last_dividend": None,
        "dividend_date": None,
    }

    try:
        page_text = page.content()

        # Dividend Yield
        yield_patterns = [
            r'Dividend\s*Yield[:\s]*(\d+\.?\d*)\s*%',
            r'Yield[:\s]*(\d+\.?\d*)\s*%',
        ]
        for pattern in yield_patterns:
            match = re.search(pattern, page_text, re.I)
            if match:
                data["dividend_yield"] = _to_float(match.group(1))
                break

        # Look for dividend amount patterns
        div_pattern = r'(?:Cash\s*)?Dividend[:\s]*(?:Rs\.?\s*)?(\d+\.?\d*)'
        match = re.search(div_pattern, page_text, re.I)
        if match:
            data["last_dividend"] = _to_float(match.group(1))

    except Exception as exc:
        logger.debug("[%s] Failed to extract dividend data: %s", ticker, exc)

    return data


def _scrape_page(page: Page, ticker: str, url: str, timeout_ms: int) -> dict[str, Any]:
    """Core scraping logic for a PSX stock page with complete data extraction."""

    # Navigate to the page
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.error("[%s] Timeout loading PSX page", ticker)
        raise ValueError(f"Timeout loading PSX page for {ticker}")

    # Wait for price element
    try:
        page.wait_for_selector(".quote__close", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.error("[%s] .quote__close selector not found", ticker)
        raise ValueError(f"PSX page structure error for {ticker}: .quote__close not found")

    # Wait for stats panel
    try:
        page.wait_for_selector(".quote__stats .stats_item", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.warning("[%s] .stats_item not found - continuing with limited data", ticker)

    # Extract price
    close_elem = page.locator(".quote__close").first
    close_txt = close_elem.text_content()
    price = _to_float(close_txt)

    if price is None or price == 0.0:
        logger.error("[%s] No valid price found: %r", ticker, close_txt)
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

    # Read basic stats from quote section
    stats = _read_stats(page, ticker)

    # 52-week range
    w52_low, w52_high = _read_52w_range(page, ticker)

    # Previous close
    if change_num is not None:
        previous_close = price - change_num
    else:
        previous_close = _to_float(stats.get("ldcp"))

    # P/E ratio from stats
    pe_ratio = _to_float(stats.get("p/e ratio (ttm)"))
    if pe_ratio is None:
        pe_ratio = _to_float(stats.get("p/e ratio"))
    if pe_ratio is None:
        pe_ratio = _to_float(stats.get("pe ratio"))

    # OHLC from stats
    open_price = _to_float(stats.get("open"))
    day_high = _to_float(stats.get("high"))
    day_low = _to_float(stats.get("low"))
    volume = _to_int(stats.get("volume"))

    # Wait a moment for dynamic content to load
    page.wait_for_timeout(500)

    # Extract additional data from page sections
    equity_data = _extract_equity_data(page, ticker)
    financials_data = _extract_financials_data(page, ticker)
    dividend_data = _extract_dividend_data(page, ticker)

    # Build result with all available data
    result = {
        "ticker": ticker.upper(),
        "market": "PSX",
        "currency": "PKR",
        "price": price,
        "previous_close": previous_close,
        "open": open_price,
        "day_high": day_high,
        "day_low": day_low,
        "volume": volume,
        "week_52_high": w52_high,
        "week_52_low": w52_low,
        "market_cap": equity_data.get("market_cap"),
        "pe_ratio": pe_ratio,
        "eps": financials_data.get("eps"),
        "dividend_yield": dividend_data.get("dividend_yield"),
        "change": change_num,
        "change_pct": change_pct,
        # Additional data fields
        "total_shares": equity_data.get("total_shares"),
        "free_float_shares": equity_data.get("free_float_shares"),
        "free_float_pct": equity_data.get("free_float_pct"),
        "net_profit_margin": financials_data.get("net_profit_margin"),
    }

    # Log what we got
    filled_fields = [k for k, v in result.items() if v is not None and v != 0]
    logger.info(
        "[%s] Scraped %d/%d fields: price=%.2f, change=%.2f%%, pe=%s, eps=%s, mcap=%s",
        ticker,
        len(filled_fields),
        len(result),
        price,
        change_pct or 0,
        pe_ratio,
        financials_data.get("eps"),
        equity_data.get("market_cap"),
    )

    return result


def _fetch_sync(ticker: str, timeout_ms: int, *, use_pool: bool = True) -> dict[str, Any]:
    """Synchronous fetch with browser pool support."""
    url = PSX_URL.format(ticker=ticker.upper())
    logger.debug("[%s] Fetching PSX quote from %s (pool=%s)", ticker, url, use_pool)

    if use_pool:
        from scrapers.browser_pool import get_browser_pool
        pool = get_browser_pool()
        with pool.get_page_sync() as page:
            return _scrape_page(page, ticker, url, timeout_ms)
    else:
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
    """Async wrapper for PSX quote fetching."""
    return await asyncio.to_thread(_fetch_sync, ticker, timeout_ms, use_pool=use_pool)
