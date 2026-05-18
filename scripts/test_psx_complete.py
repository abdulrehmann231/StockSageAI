#!/usr/bin/env python3
"""Test PSX scraper with 10 diverse stocks to verify complete data extraction."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add backend to path
BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scrapers.psx_prices import fetch_psx_quote

# 10 diverse PSX stocks across different sectors and price ranges
TICKERS = [
    "ENGRO",   # Fertilizer - mid price
    "HBL",     # Banking - mid price
    "OGDC",    # Oil & Gas - mid price
    "LUCK",    # Cement - mid price
    "NESTLE",  # Food - HIGH price (~7500 PKR) - tests 52w range fix
    "SYS",     # Technology - mid price
    "FFC",     # Fertilizer - mid price
    "MEBL",    # Banking - mid price
    "PSO",     # Oil & Gas - mid price
    "HUBC",    # Power - mid price
]

# Fields we expect to have data for
CORE_FIELDS = [
    "ticker", "market", "currency", "price", "previous_close",
    "change", "change_pct"
]

OHLC_FIELDS = ["open", "day_high", "day_low", "volume"]

FUNDAMENTAL_FIELDS = [
    "week_52_high", "week_52_low", "market_cap", "pe_ratio", "eps",
    "total_shares", "free_float_shares", "free_float_pct"
]

OPTIONAL_FIELDS = ["dividend_yield", "net_profit_margin"]


def format_number(val, decimals=2):
    """Format number for display."""
    if val is None:
        return "None"
    if isinstance(val, float):
        if val >= 1_000_000_000:
            return f"{val/1_000_000_000:.2f}B"
        if val >= 1_000_000:
            return f"{val/1_000_000:.2f}M"
        if val >= 1_000:
            return f"{val/1_000:.2f}K"
        return f"{val:.{decimals}f}"
    return str(val)


async def test_single_ticker(ticker: str) -> dict:
    """Test a single ticker and return results."""
    try:
        quote = await fetch_psx_quote(ticker, timeout_ms=60_000)
        return {"ticker": ticker, "success": True, "data": quote, "error": None}
    except Exception as e:
        return {"ticker": ticker, "success": False, "data": None, "error": str(e)}


async def main():
    print("=" * 80)
    print("PSX COMPLETE DATA EXTRACTION TEST")
    print("=" * 80)
    print(f"\nTesting {len(TICKERS)} stocks: {', '.join(TICKERS)}\n")

    results = []
    for ticker in TICKERS:
        print(f"Fetching {ticker}...", end=" ", flush=True)
        result = await test_single_ticker(ticker)
        results.append(result)
        if result["success"]:
            print(f"✅ price={format_number(result['data']['price'])}")
        else:
            print(f"❌ {result['error']}")

    print("\n" + "=" * 80)
    print("DETAILED RESULTS")
    print("=" * 80)

    # Analyze field coverage
    field_coverage = {field: 0 for field in CORE_FIELDS + OHLC_FIELDS + FUNDAMENTAL_FIELDS + OPTIONAL_FIELDS}
    successful = [r for r in results if r["success"]]

    for result in successful:
        data = result["data"]
        for field in field_coverage:
            if data.get(field) is not None and data.get(field) != 0:
                field_coverage[field] += 1

    # Print per-stock summary
    print("\n📊 PER-STOCK DATA SUMMARY\n")
    print(f"{'Ticker':<8} {'Price':>10} {'Change%':>8} {'52wL':>8} {'52wH':>10} {'MCap':>10} {'P/E':>6} {'EPS':>8} {'Shares':>10}")
    print("-" * 90)

    for result in results:
        if not result["success"]:
            print(f"{result['ticker']:<8} FAILED: {result['error'][:50]}")
            continue

        d = result["data"]
        print(
            f"{d['ticker']:<8} "
            f"{format_number(d['price']):>10} "
            f"{format_number(d.get('change_pct')):>8} "
            f"{format_number(d.get('week_52_low')):>8} "
            f"{format_number(d.get('week_52_high')):>10} "
            f"{format_number(d.get('market_cap')):>10} "
            f"{format_number(d.get('pe_ratio')):>6} "
            f"{format_number(d.get('eps')):>8} "
            f"{format_number(d.get('total_shares')):>10}"
        )

    # Print field coverage
    print("\n" + "=" * 80)
    print("FIELD COVERAGE ANALYSIS")
    print("=" * 80)

    total_successful = len(successful)

    print(f"\n✅ Core Fields (should be 100%):")
    for field in CORE_FIELDS:
        pct = (field_coverage[field] / total_successful * 100) if total_successful > 0 else 0
        status = "✅" if pct == 100 else "⚠️" if pct >= 50 else "❌"
        print(f"  {status} {field:<20}: {field_coverage[field]}/{total_successful} ({pct:.0f}%)")

    print(f"\n📈 OHLC Fields:")
    for field in OHLC_FIELDS:
        pct = (field_coverage[field] / total_successful * 100) if total_successful > 0 else 0
        status = "✅" if pct >= 80 else "⚠️" if pct >= 50 else "❌"
        print(f"  {status} {field:<20}: {field_coverage[field]}/{total_successful} ({pct:.0f}%)")

    print(f"\n💰 Fundamental Fields:")
    for field in FUNDAMENTAL_FIELDS:
        pct = (field_coverage[field] / total_successful * 100) if total_successful > 0 else 0
        status = "✅" if pct >= 80 else "⚠️" if pct >= 50 else "❌"
        print(f"  {status} {field:<20}: {field_coverage[field]}/{total_successful} ({pct:.0f}%)")

    print(f"\n📋 Optional Fields:")
    for field in OPTIONAL_FIELDS:
        pct = (field_coverage[field] / total_successful * 100) if total_successful > 0 else 0
        status = "✅" if pct >= 50 else "⚠️" if pct >= 25 else "ℹ️"
        print(f"  {status} {field:<20}: {field_coverage[field]}/{total_successful} ({pct:.0f}%)")

    # Special check for NESTLE 52w range (the bug we fixed)
    print("\n" + "=" * 80)
    print("NESTLE 52W RANGE VALIDATION (Bug Fix Check)")
    print("=" * 80)

    nestle = next((r for r in results if r["ticker"] == "NESTLE" and r["success"]), None)
    if nestle:
        d = nestle["data"]
        price = d["price"]
        w52_low = d.get("week_52_low")
        w52_high = d.get("week_52_high")

        print(f"\n  Current Price: {format_number(price)}")
        print(f"  52w Low:       {format_number(w52_low)}")
        print(f"  52w High:      {format_number(w52_high)}")

        if w52_low and w52_high:
            if w52_low > 1000 and w52_high > 5000:
                print(f"\n  ✅ 52w range looks correct (values in thousands as expected for NESTLE)")
            else:
                print(f"\n  ❌ 52w range may be incorrect (expected values > 1000 for NESTLE)")
        else:
            print(f"\n  ⚠️ 52w range data missing")
    else:
        print("\n  ❌ NESTLE data not available")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\n  Total Stocks Tested: {len(TICKERS)}")
    print(f"  Successful:          {len(successful)}")
    print(f"  Failed:              {len(TICKERS) - len(successful)}")

    # Calculate overall coverage
    total_fields = len(CORE_FIELDS) + len(OHLC_FIELDS) + len(FUNDAMENTAL_FIELDS)
    filled_fields = sum(1 for f in CORE_FIELDS + OHLC_FIELDS + FUNDAMENTAL_FIELDS
                        if field_coverage[f] >= total_successful * 0.8)
    print(f"\n  Fields with ≥80% coverage: {filled_fields}/{total_fields}")

    if len(successful) == len(TICKERS) and filled_fields >= total_fields * 0.8:
        print("\n  🎉 PSX SCRAPER TEST PASSED!")
    else:
        print("\n  ⚠️ Some issues detected - review results above")


if __name__ == "__main__":
    asyncio.run(main())
