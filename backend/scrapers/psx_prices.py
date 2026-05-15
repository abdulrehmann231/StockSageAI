"""Playwright-based price scraper for the Pakistan Stock Exchange.

Targets dps.psx.com.pk/company/<TICKER>. The site is JS-rendered so we
launch headless Chromium, parse the visible quote block, then walk
``.stats_item`` rows by label.

Windows note: the project uses ``WindowsSelectorEventLoopPolicy`` for
psycopg-async, but Playwright's worker-thread loop needs subprocess
support. We swap to the Proactor policy just for the duration of the
scrape.
"""

from __future__ import annotations

import asyncio
import re
import sys
from contextlib import contextmanager
from typing import Any

from playwright.sync_api import Page, sync_playwright

PSX_URL = "https://dps.psx.com.pk/company/{ticker}"


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


_FIRST_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+\.?\d*")


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


def _read_stats(page: Page) -> dict[str, str]:
    """Walk every ``.stats_item`` in the quote block.

    Returns a {label: value_text} map keyed on the lowercased label so
    callers can look up "open", "high", "p/e ratio (ttm)", etc.
    """
    items = page.locator(".quote__stats .stats_item")
    count = items.count()
    out: dict[str, str] = {}
    for i in range(count):
        item = items.nth(i)
        try:
            label = item.locator(".stats_label").first.text_content(timeout=1_000) or ""
            value = item.locator(".stats_value").first.text_content(timeout=1_000) or ""
        except Exception:
            continue
        out[label.strip().lower()] = value.strip()
    return out


def _read_52w_range(page: Page) -> tuple[float | None, float | None]:
    """52-week range stat exposes data-low/data-high on its inner numRange."""
    items = page.locator(".quote__stats .stats_item")
    for i in range(items.count()):
        item = items.nth(i)
        try:
            label = (item.locator(".stats_label").first.text_content(timeout=500) or "").strip().lower()
        except Exception:
            continue
        if "52" in label and "week" in label and "range" in label:
            try:
                numrange = item.locator(".numRange").first
                low = numrange.get_attribute("data-low", timeout=500)
                high = numrange.get_attribute("data-high", timeout=500)
                return _to_float(low), _to_float(high)
            except Exception:
                return None, None
    return None, None


def _fetch_sync(ticker: str, timeout_ms: int) -> dict[str, Any]:
    url = PSX_URL.format(ticker=ticker.upper())

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
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector(".quote__close", timeout=timeout_ms)
            # Stats panel is rendered after initial paint.
            page.wait_for_selector(".quote__stats .stats_item", timeout=timeout_ms)

            close_txt = page.locator(".quote__close").first.text_content()
            change_txt = page.locator(".quote__change").first.text_content() or ""

            price = _to_float(close_txt)
            if price is None or price == 0.0:
                raise ValueError(f"PSX page returned no price for {ticker}")

            change_num: float | None = None
            change_pct: float | None = None
            stripped = change_txt.strip()
            if stripped:
                first_token = stripped.split()[0] if stripped.split() else None
                change_num = _to_float(first_token)
                pct_match = re.search(r"\(([-+]?\d+\.?\d*)\s*%\)", stripped)
                if pct_match:
                    change_pct = float(pct_match.group(1))
                if "neg" in (
                    page.locator(".quote__change").first.get_attribute("class") or ""
                ):
                    if change_num is not None and change_num > 0:
                        change_num = -change_num
                    if change_pct is not None and change_pct > 0:
                        change_pct = -change_pct

            stats = _read_stats(page)
            w52_low, w52_high = _read_52w_range(page)

            # PSX sometimes shows yesterday's LDCP equal to today's price even
            # when a change is reported. Prefer the change-implied prev close
            # so the displayed delta stays self-consistent.
            if change_num is not None:
                previous_close = price - change_num
            else:
                previous_close = _to_float(stats.get("ldcp"))

            return {
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
                "market_cap": None,  # not surfaced on this page
                "pe_ratio": _to_float(stats.get("p/e ratio (ttm)")),
                "eps": None,
                "dividend_yield": None,
                "change": change_num,
                "change_pct": change_pct,
            }
        finally:
            browser.close()


async def fetch_psx_quote(ticker: str, *, timeout_ms: int = 30_000) -> dict[str, Any]:
    """Async wrapper that offloads the sync Playwright run to a thread."""
    return await asyncio.to_thread(_fetch_sync, ticker, timeout_ms)
