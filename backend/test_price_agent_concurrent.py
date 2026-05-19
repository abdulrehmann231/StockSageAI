"""Concurrent stress test for the browser-pool threading fix.

Fires N PSX scrapes simultaneously via asyncio.gather. With the old
asyncio.to_thread routing, this would crash every call after the first
with `greenlet.error: Cannot switch to a different thread`. With the
pinned-thread pool the calls should queue and all succeed.
"""

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


TICKERS = ["MARI", "OGDC", "UBL", "LUCK", "HBL"]


async def run_one(ticker: str) -> tuple[str, bool, float, str]:
    start = time.perf_counter()
    try:
        q = await get_price(ticker, "PSX", use_cache=False)
        elapsed = (time.perf_counter() - start) * 1000
        return ticker, True, elapsed, f"price={q.price} {q.currency}"
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - start) * 1000
        return ticker, False, elapsed, f"{type(exc).__name__}: {exc}"


async def main() -> int:
    print(f"Firing {len(TICKERS)} concurrent PSX scrapes via asyncio.gather...\n")
    started = time.perf_counter()

    results = await asyncio.gather(*(run_one(t) for t in TICKERS))

    total_ms = (time.perf_counter() - started) * 1000
    print()
    print("=" * 72)
    passes = sum(1 for _, ok, _, _ in results if ok)
    for ticker, ok, elapsed, msg in results:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {ticker:<8} {elapsed:>8.0f} ms   {msg}")
    print(f"\n  {passes}/{len(results)} passed in {total_ms:.0f} ms wall-clock")
    print("=" * 72)

    await cache_service.close()
    await close_browser_pool()
    return 0 if passes == len(results) else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
