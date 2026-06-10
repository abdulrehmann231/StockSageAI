"""Playwright-based price scraper for the Pakistan Stock Exchange.

Targets dps.psx.com.pk/company/<TICKER>. The site is JS-rendered so we
launch headless Chromium, parse all stats_item elements across the page,
and extract comprehensive data.

Features:
- Complete data extraction from stats_item elements throughout the page
- Fixed 52w range parsing for high-priced tickers
- Market cap, EPS, shares, P/E ratio extraction
- Comprehensive logging for selector failures
- Browser pool support for better performance
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from contextlib import contextmanager
from datetime import date, datetime
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


def _normalize_label(label: str) -> str:
    """Normalize a label for consistent lookup."""
    return label.strip().lower().replace("'", "").replace("(", "").replace(")", "")


# "As of Fri, Jan 3, 2025 4:49 PM" -> capture "Jan", "3", "2025"
_AS_OF_RE = re.compile(
    r"As of\s+\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})",
    re.IGNORECASE,
)


def _detect_listing_status(page_text: str, ticker: str) -> tuple[bool, date | None]:
    """Detect the DELISTED badge and the 'As of <date>' stamp.

    The badge text gets concatenated with neighbouring words in the body's
    ``text_content`` (e.g. ``LimitedDELISTEDFERTILIZER``) so a word-boundary
    regex won't match it. Case-sensitive uppercase substring keeps us from
    false-matching the lowercase "delisted from the Exchange" disclaimer
    paragraph that appears on every PSX company page.
    """
    is_delisted = "DELISTED" in page_text

    data_as_of: date | None = None
    match = _AS_OF_RE.search(page_text)
    if match:
        month_str, day_str, year_str = match.groups()
        try:
            data_as_of = datetime.strptime(
                f"{month_str} {day_str} {year_str}", "%b %d %Y"
            ).date()
        except ValueError:
            try:
                data_as_of = datetime.strptime(
                    f"{month_str} {day_str} {year_str}", "%B %d %Y"
                ).date()
            except ValueError as exc:
                logger.debug("[%s] Could not parse as-of date %r: %s",
                             ticker, match.group(0), exc)

    if is_delisted:
        logger.info("[%s] DELISTED detected; data_as_of=%s", ticker, data_as_of)
    return is_delisted, data_as_of


def _read_all_stats(page: Page, ticker: str) -> dict[str, str]:
    """Read ALL stats_item elements from the entire page.

    The PSX page has multiple sections (Quote, Equity Profile, etc.)
    all using the same stats_item structure. This function collects
    all of them into a single dictionary.

    Includes retry logic: if first attempt returns empty/partial results,
    waits briefly and retries to handle async JS rendering delays.
    """
    out = _read_all_stats_attempt(page, ticker)

    # If we got very few results, retry after a short wait (page may still be loading)
    if len(out) < 3:
        logger.debug("[%s] Only %d stats found, retrying after delay", ticker, len(out))
        page.wait_for_timeout(2000)
        out = _read_all_stats_attempt(page, ticker)

    return out


def _read_all_stats_attempt(page: Page, ticker: str) -> dict[str, str]:
    """Single attempt to read all stats_item elements from the page."""
    # Get all stats_item elements on the page (not just in quote__stats)
    items = page.locator(".stats_item")
    try:
        count = items.count()
    except Exception as exc:
        logger.warning("[%s] Failed to count .stats_item elements: %s", ticker, exc)
        return {}

    logger.debug("[%s] Found %d stats_item elements on page", ticker, count)

    out: dict[str, str] = {}
    for i in range(count):
        item = items.nth(i)
        try:
            label_elem = item.locator(".stats_label").first
            value_elem = item.locator(".stats_value").first

            # Use longer timeout (3s) to handle slow rendering
            label = label_elem.text_content(timeout=3_000) or ""
            value = value_elem.text_content(timeout=3_000) or ""

            normalized = _normalize_label(label)
            if normalized and value.strip():
                out[normalized] = value.strip()
                logger.debug("[%s] Stats: '%s' = '%s'", ticker, normalized, value.strip()[:50])
        except Exception as exc:
            logger.debug("[%s] Failed to read stats_item %d: %s", ticker, i, exc)
            continue

    if not out:
        logger.warning("[%s] No stats items found - PSX page structure may have changed", ticker)

    return out


def _extract_52w_range(stats: dict[str, str], ticker: str) -> tuple[float | None, float | None]:
    """Extract 52-week range from stats dictionary."""
    # Look for 52-week range in various possible key formats
    for key in stats:
        if "52" in key and "week" in key and "range" in key:
            value = stats[key]
            low, high = _parse_range_text(value)
            if low is not None and high is not None:
                logger.debug("[%s] 52w range: %.2f - %.2f", ticker, low, high)
                return low, high
    return None, None


def _scrape_page(page: Page, ticker: str, url: str, timeout_ms: int) -> dict[str, Any]:
    """Core scraping logic for a PSX stock page with complete data extraction."""

    # Navigate to the page
    try:
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.error("[%s] Timeout loading PSX page", ticker)
        raise ValueError(f"Timeout loading PSX page for {ticker}")

    # Wait for price element first
    try:
        page.wait_for_selector(".quote__close", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.error("[%s] .quote__close selector not found", ticker)
        raise ValueError(f"PSX page structure error for {ticker}: .quote__close not found")

    # Wait for stats items to load (they appear after the main price section)
    try:
        page.wait_for_selector(".stats_item", timeout=min(10_000, timeout_ms))
    except PlaywrightTimeout:
        logger.warning("[%s] .stats_item elements not found within timeout - page may not have stats", ticker)

    # Additional wait for any remaining async JS to populate stat values
    page.wait_for_timeout(1500)

    # Extract price
    close_elem = page.locator(".quote__close").first
    close_txt = close_elem.text_content()
    price = _to_float(close_txt)

    if price is None or price == 0.0:
        logger.error("[%s] No valid price found: %r", ticker, close_txt)
        raise ValueError(f"PSX page returned no price for {ticker}")

    # Extract change
    try:
        change_elem = page.locator(".quote__change").first
        change_elem.wait_for(timeout=3_000)
        change_txt = change_elem.text_content() or ""
    except Exception as exc:
        logger.debug("[%s] Could not read .quote__change: %s", ticker, exc)
        change_txt = ""
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

    # Read ALL stats from the page (Quote section + Equity Profile + others)
    stats = _read_all_stats(page, ticker)

    # 52-week range
    w52_low, w52_high = _extract_52w_range(stats, ticker)

    # Previous close
    if change_num is not None:
        previous_close = price - change_num
    else:
        previous_close = _to_float(stats.get("ldcp"))

    # OHLC from stats
    open_price = _to_float(stats.get("open"))
    day_high = _to_float(stats.get("high"))
    day_low = _to_float(stats.get("low"))
    volume = _to_int(stats.get("volume"))

    # P/E ratio - try various key formats (label may have ** suffix)
    pe_ratio = None
    for key in stats:
        if "p/e ratio" in key or "pe ratio" in key:
            pe_ratio = _to_float(stats[key])
            if pe_ratio:
                logger.debug("[%s] P/E ratio: %.2f (from '%s')", ticker, pe_ratio, key)
                break

    # Market Cap (PSX shows in 000's, so multiply by 1000)
    market_cap = None
    for key in stats:
        if "market cap" in key or "market capitalization" in key:
            raw_cap = _to_float(stats[key])
            if raw_cap:
                market_cap = raw_cap * 1000  # Convert from thousands
                logger.debug("[%s] Market cap: %.0f (from '%s')", ticker, market_cap, key)
                break

    # Total Shares
    total_shares = None
    for key in stats:
        if key == "shares" or "total shares" in key:
            total_shares = _to_int(stats[key])
            if total_shares:
                break

    # Free Float - need to handle multiple entries with same label
    # One shows absolute shares, one shows percentage
    free_float_shares = None
    free_float_pct = None

    # Try to extract from stats dict first (most reliable)
    for key in stats:
        if "free float" in key:
            value_str = stats[key]
            if "%" in value_str:
                free_float_pct = _to_float(value_str)
            elif not free_float_shares:
                free_float_shares = _to_int(value_str)

    # If not found in stats, iterate through page items directly
    if free_float_shares is None and free_float_pct is None:
        try:
            ff_items = page.locator(".stats_item").all()
            for item in ff_items:
                try:
                    label_elem = item.locator(".stats_label").first
                    label = (label_elem.text_content(timeout=1_000) or "").strip().lower()
                    if "free float" not in label:
                        continue
                    value_elem = item.locator(".stats_value").first
                    value = (value_elem.text_content(timeout=1_000) or "").strip()
                    if "%" in value:
                        free_float_pct = _to_float(value)
                    elif value and not free_float_shares:
                        free_float_shares = _to_int(value)
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("[%s] Failed to extract free float from page: %s", ticker, exc)

    # EPS - look in stats first, then in page text (Financials table)
    eps = None
    for key in stats:
        if key == "eps" or "earnings per share" in key:
            eps = _to_float(stats[key])
            if eps:
                logger.debug("[%s] EPS from stats: %.2f", ticker, eps)
                break

    # If not found in stats, try to extract from page text (Financials section)
    if not eps:
        try:
            page_text = page.locator("body").text_content() or ""
            # Look for EPS followed by a number (annual EPS in Financials table)
            # Try multiple patterns for robustness
            eps_patterns = [
                r'EPS\s*(?:\(Rs\.\))?\s*(-?\d+\.?\d*)',  # "EPS 5.23" or "EPS (Rs.) 5.23"
                r'EPS\s*:\s*(-?\d+\.?\d*)',  # "EPS: 5.23"
                r'Earnings\s*Per\s*Share\s*(-?\d+\.?\d*)',  # "Earnings Per Share 5.23"
            ]
            for pattern in eps_patterns:
                eps_match = re.search(pattern, page_text, re.IGNORECASE)
                if eps_match:
                    eps = _to_float(eps_match.group(1))
                    if eps is not None:
                        logger.debug("[%s] EPS from page text: %.2f", ticker, eps)
                        break
        except Exception as exc:
            logger.debug("[%s] Failed to extract EPS from page text: %s", ticker, exc)

    # Net Profit Margin - check stats first, then page text (Financials table)
    net_profit_margin = None
    for key in stats:
        if "net profit margin" in key or "profit margin" in key:
            net_profit_margin = _to_float(stats[key])
            if net_profit_margin:
                logger.debug("[%s] Net Profit Margin from stats: %.2f", ticker, net_profit_margin)
                break

    # If not found in stats, extract from page text (Financials section)
    # Format on page: "Net Profit Margin (%)9.84" - value follows immediately
    if not net_profit_margin:
        try:
            page_text = page.locator("body").text_content() or ""
            # Match "Net Profit Margin (%)" followed by number (possibly negative)
            npm_match = re.search(r'Net\s*Profit\s*Margin\s*\(%?\)?\s*(-?\d+\.?\d*)', page_text, re.I)
            if npm_match:
                net_profit_margin = _to_float(npm_match.group(1))
                if net_profit_margin:
                    logger.debug("[%s] Net Profit Margin from page text: %.2f", ticker, net_profit_margin)
        except Exception as exc:
            logger.debug("[%s] Failed to extract Net Profit Margin from page text: %s", ticker, exc)

    # Dividend Yield - check stats first, then calculate from payout data
    dividend_yield = None
    for key in stats:
        if "dividend yield" in key or "div yield" in key:
            dividend_yield = _to_float(stats[key])
            if dividend_yield:
                logger.debug("[%s] Dividend Yield from stats: %.2f", ticker, dividend_yield)
                break

    # If not found in stats, calculate from payout percentage
    # PSX shows dividends as percentage of face value (typically Rs. 10)
    # Example: "60%(F)" means 60% of Rs. 10 = Rs. 6 per share
    # Dividend Yield = (Annual DPS / Current Price) × 100
    if not dividend_yield and price:
        try:
            page_text = page.locator("body").text_content() or ""
            # Look for payout percentages in Payouts section
            # Pattern: "(YR) 60%(F)" or "(HYR) 45%(ii)" etc.
            # We want the most recent full year (YR) dividend or sum of interim dividends

            # Find all dividend payout percentages with their types
            # Format: "31/12/2025(YR) 60%(F)" or "30/09/2025(IIIQ) 50%(iii)"
            payout_matches = re.findall(
                r'\((?:YR|HYR|IQ|IIQ|IIIQ)\)\s*(\d+(?:\.\d+)?)\s*%\s*\(([FfiD]|[iv]+)\)',
                page_text,
                re.I
            )

            if payout_matches:
                # Sum up annual dividend (Final + all interim dividends from most recent year)
                # For simplicity, take the most recent Final (F) dividend as annual dividend
                annual_dividend_pct = 0
                for pct, div_type in payout_matches:
                    if div_type.upper() == 'F':  # Final dividend
                        annual_dividend_pct = float(pct)
                        break

                # If no Final found, sum recent interim dividends
                if not annual_dividend_pct and payout_matches:
                    # Take the first (most recent) payout as a proxy
                    annual_dividend_pct = float(payout_matches[0][0])

                if annual_dividend_pct > 0:
                    # Face value in Pakistan is typically Rs. 10
                    face_value = 10.0
                    dps = (annual_dividend_pct / 100) * face_value
                    dividend_yield = (dps / price) * 100
                    logger.debug(
                        "[%s] Dividend Yield calculated: %.2f%% (DPS=%.2f from %d%% of Rs.%.0f, price=%.2f)",
                        ticker, dividend_yield, dps, int(annual_dividend_pct), face_value, price
                    )
        except Exception as exc:
            logger.debug("[%s] Failed to calculate Dividend Yield: %s", ticker, exc)

    # Listing status (DELISTED badge + "As of <date>" stamp)
    try:
        page_text = page.locator("body").text_content() or ""
        is_delisted, data_as_of = _detect_listing_status(page_text, ticker)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[%s] Listing-status detection failed: %s", ticker, exc)
        is_delisted, data_as_of = False, None

    # Build result
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
        "market_cap": market_cap,
        "pe_ratio": pe_ratio,
        "eps": eps,
        "dividend_yield": dividend_yield,
        "change": change_num,
        "change_pct": change_pct,
        "total_shares": total_shares,
        "free_float_shares": free_float_shares,
        "free_float_pct": free_float_pct,
        "net_profit_margin": net_profit_margin,
        "is_delisted": is_delisted,
        "data_as_of": data_as_of,
    }

    # Log summary
    filled = [k for k, v in result.items() if v is not None and v != 0]
    logger.info(
        "[%s] Scraped %d/%d fields: price=%.2f, mcap=%s, pe=%s, eps=%s, shares=%s",
        ticker, len(filled), len(result),
        price,
        f"{market_cap:.0f}" if market_cap else "None",
        pe_ratio,
        eps,
        total_shares,
    )

    return result


def _fetch_sync_no_pool(ticker: str, timeout_ms: int) -> dict[str, Any]:
    """One-shot fetch without the pool. Used when use_pool=False."""
    url = PSX_URL.format(ticker=ticker.upper())
    logger.debug("[%s] Fetching PSX quote from %s (no pool)", ticker, url)
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
    if use_pool:
        from scrapers.browser_pool import get_browser_pool

        pool = get_browser_pool()
        url = PSX_URL.format(ticker=ticker.upper())
        logger.debug("[%s] Fetching PSX quote from %s (pool)", ticker, url)
        return await pool.run_with_page_async(
            lambda page: _scrape_page(page, ticker, url, timeout_ms)
        )
    return await asyncio.to_thread(_fetch_sync_no_pool, ticker, timeout_ms)
