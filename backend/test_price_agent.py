"""Manual test for price_agent.get_price across global + PSX tickers."""

from __future__ import annotations

import asyncio
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from agents.price_agent import get_price
from scrapers.browser_pool import close_browser_pool
from services import cache_service


GLOBAL_TICKERS = ["NVDA", "TSLA", "META"]
PSX_TICKERS = ["OGDC", "MARI", "UBL"]


def _fmt(value, *, money: bool = False, pct: bool = False, integer: bool = False) -> str:
    if value is None:
        return "—"
    if integer:
        return f"{int(value):,}"
    if pct:
        return f"{value:+.2f}%"
    if money:
        return f"{value:,.2f}"
    return f"{value}"


def print_quote(quote, elapsed_ms: float) -> None:
    print("=" * 72)
    print(f"  {quote.ticker}  ({quote.market})   source={quote.source}   "
          f"cached={quote.cached}   fetched in {elapsed_ms:.0f} ms")
    print("-" * 72)
    print(f"  Price          : {_fmt(quote.price, money=True)} {quote.currency or ''}")
    print(f"  Previous close : {_fmt(quote.previous_close, money=True)}")
    print(f"  Change         : {_fmt(quote.change, money=True)}  "
          f"({_fmt(quote.change_pct, pct=True)})")
    print(f"  Open / High / Low : {_fmt(quote.open, money=True)} / "
          f"{_fmt(quote.day_high, money=True)} / {_fmt(quote.day_low, money=True)}")
    print(f"  Volume         : {_fmt(quote.volume, integer=True)}")
    print(f"  52w high / low : {_fmt(quote.week_52_high, money=True)} / "
          f"{_fmt(quote.week_52_low, money=True)}")
    print(f"  Market cap     : {_fmt(quote.market_cap, integer=True)}")
    print(f"  P/E            : {_fmt(quote.pe_ratio, money=True)}")
    print(f"  EPS            : {_fmt(quote.eps, money=True)}")
    print(f"  Dividend yield : {_fmt(quote.dividend_yield, pct=True)}")
    if quote.market == "PSX":
        print(f"  Total shares   : {_fmt(quote.total_shares, integer=True)}")
        print(f"  Free float     : {_fmt(quote.free_float_shares, integer=True)} "
              f"({_fmt(quote.free_float_pct, pct=True)})")
        print(f"  Net margin     : {_fmt(quote.net_profit_margin, pct=True)}")


async def run_one(ticker: str, market: str) -> tuple[str, bool, str]:
    start = time.perf_counter()
    try:
        quote = await get_price(ticker, market, use_cache=False)
        elapsed_ms = (time.perf_counter() - start) * 1000
        print_quote(quote, elapsed_ms)
        return ticker, True, "ok"
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - start) * 1000
        print("=" * 72)
        print(f"  {ticker}  ({market})   FAILED in {elapsed_ms:.0f} ms")
        print(f"  {type(exc).__name__}: {exc}")
        return ticker, False, f"{type(exc).__name__}: {exc}"


async def main() -> int:
    results: list[tuple[str, bool, str]] = []

    print("\n### GLOBAL TICKERS (yfinance) ###\n")
    for t in GLOBAL_TICKERS:
        results.append(await run_one(t, "GLOBAL"))

    print("\n### PSX TICKERS (Playwright scrape) ###\n")
    for t in PSX_TICKERS:
        results.append(await run_one(t, "PSX"))

    print("\n" + "=" * 72)
    print("  SUMMARY")
    print("-" * 72)
    passes = sum(1 for _, ok, _ in results if ok)
    for ticker, ok, msg in results:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {ticker:<8} {msg}")
    print(f"\n  {passes}/{len(results)} passed")
    print("=" * 72)

    # Cleanup
    await cache_service.close()
    await close_browser_pool()

    return 0 if passes == len(results) else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        # Playwright sync API requires the Proactor loop on Windows.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
