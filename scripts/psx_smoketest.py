"""One-off PSX scraper smoke test: hit a diverse set of tickers and print
which fields populate so we can decide whether to add a second-scrape
for fundamentals.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scrapers.psx_prices import fetch_psx_quote  # noqa: E402

TICKERS = ["ENGRO", "HBL", "OGDC", "LUCK", "NESTLE", "SYS", "FFC", "MEBL"]
FIELDS = [
    "price",
    "previous_close",
    "open",
    "day_high",
    "day_low",
    "volume",
    "week_52_high",
    "week_52_low",
    "market_cap",
    "pe_ratio",
    "eps",
    "dividend_yield",
    "change",
    "change_pct",
]


async def main() -> None:
    rows: list[tuple[str, dict]] = []
    for tkr in TICKERS:
        print(f"fetching {tkr}...", flush=True)
        try:
            q = await fetch_psx_quote(tkr, timeout_ms=45_000)
            rows.append((tkr, q))
        except Exception as exc:
            print(f"  ERROR: {exc}")
            rows.append((tkr, {}))

    # Per-field fill rate
    print("\n=== fill rate across tickers ===")
    for f in FIELDS:
        present = sum(1 for _, q in rows if q.get(f) not in (None, 0, 0.0))
        print(f"  {f:18}  {present}/{len(rows)}")

    print("\n=== per-ticker snapshot ===")
    for tkr, q in rows:
        if not q:
            print(f"{tkr}: FAILED")
            continue
        print(
            f"{tkr:8} price={q.get('price')!s:10} chg={q.get('change_pct')!s:8}  "
            f"open={q.get('open')!s:10} vol={q.get('volume')!s:12} "
            f"52w=[{q.get('week_52_low')}, {q.get('week_52_high')}]  "
            f"PE={q.get('pe_ratio')} EPS={q.get('eps')} "
            f"mcap={q.get('market_cap')} divY={q.get('dividend_yield')}"
        )


if __name__ == "__main__":
    asyncio.run(main())
