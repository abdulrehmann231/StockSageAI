"""Real-world stress test — diverse tickers, edge cases, production readiness.

Tests:
    1. PSX blue-chip    — OGDC  (Oil & Gas Dev Co, high news volume)
    2. PSX bank         — HBL   (Habib Bank, financial sector)
    3. PSX commodity    — MARI  (Mari Petroleum, energy)
    4. Global large-cap — NVDA  (Nvidia, hottest stock right now)
    5. Global financial — JPM   (JP Morgan, financial sector)
    6. Concurrent load  — 3 tickers simultaneously (production simulation)
    7. Cache efficiency — repeated calls should be instant

Usage (from backend/):
    python test_stress.py
"""

import asyncio
import time
from datetime import datetime, timezone

from agents.news_agent2 import get_news, NewsImpact

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"


def banner(text):
    print(f"\n{BOLD}{CYAN}{'─' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 60}{RESET}\n")


def print_result(result):
    print(f"  Ticker           : {result.ticker}")
    print(f"  Market           : {result.market}")
    print(f"  Cached           : {result.cached}")
    print(f"  Overall Sentiment: {result.overall_news_sentiment.value}")
    print(f"  Top Catalyst     : {result.top_catalyst}")
    print(f"  Sources          : {', '.join(result.sources) if result.sources else 'none'}")
    print(f"  Article count    : {len(result.articles)}")
    print(f"  Errors           : {result.errors if result.errors else 'none'}\n")
    for i, a in enumerate(result.articles, 1):
        age = f"{(datetime.now(timezone.utc) - a.published_at).days}d ago" if a.published_at else "no date"
        print(f"  [{i}] {a.title}")
        print(f"       Source    : {a.source}  |  {age}")
        print(f"       Impact    : {a.impact.value}")
        print(f"       Catalysts : {a.catalysts or 'none'}")
        print(f"       Summary   : {a.summary[:200]}{'...' if len(a.summary) > 200 else ''}\n")


def check(label, ok, detail=""):
    icon = f"{GREEN}✅{RESET}" if ok else f"{RED}❌{RESET}"
    suffix = f"  ({detail})" if detail else ""
    print(f"    {icon}  {label}{suffix}")


def section_checks(label, result, market, elapsed, expected_sources_hint):
    print(f"  {BOLD}Checks:{RESET}")
    check("No crash",                      result is not None)
    check("Correct ticker",                result.ticker == label)
    check("Correct market",                result.market == market)
    check("Sentiment is set",              result.overall_news_sentiment in NewsImpact)
    check("At least 1 article",            len(result.articles) >= 1)
    check("Max 5 articles",                len(result.articles) <= 5)
    check("All articles have titles",      all(a.title for a in result.articles))
    check("All articles have summaries",   all(a.summary for a in result.articles))
    check("All articles have URLs",        all(a.url for a in result.articles))
    check("All articles have impact",      all(a.impact in NewsImpact for a in result.articles))
    check("No template summaries",         not any(
        "is the key company-specific development" in a.summary or
        "gives investors a concrete company-specific event" in a.summary
        for a in result.articles
    ))
    check("No articles older than 90 days", all(
        (datetime.now(timezone.utc) - a.published_at).days <= 90
        for a in result.articles if a.published_at
    ))
    check(f"Expected source present ({expected_sources_hint})", any(
        expected_sources_hint.lower() in s.lower()
        for s in result.sources
    ))
    check("Completed within 60s",          elapsed < 60, f"{elapsed:.1f}s")
    print()


async def main():

    # ─────────────────────────────────────────
    banner("TEST 1 — PSX: OGDC (Oil & Gas Dev Co)")
    # ─────────────────────────────────────────
    t0 = time.perf_counter()
    r = await get_news("OGDC", "PSX", company_name="Oil and Gas Development Company", use_cache=False)
    elapsed = time.perf_counter() - t0
    print_result(r)
    section_checks("OGDC", r, "PSX", elapsed, "profit")

    print(f"  {YELLOW}👆 Are articles about OGDC specifically? Is sentiment reasonable for an oil company?{RESET}\n")
    input(f"  Press Enter to continue...\n")


    # ─────────────────────────────────────────
    banner("TEST 2 — PSX: HBL (Habib Bank Limited)")
    # ─────────────────────────────────────────
    t0 = time.perf_counter()
    r = await get_news("HBL", "PSX", company_name="Habib Bank Limited", use_cache=False)
    elapsed = time.perf_counter() - t0
    print_result(r)
    section_checks("HBL", r, "PSX", elapsed, "profit")

    print(f"  {YELLOW}👆 Banking news — look for earnings/dividend catalysts. No generic 'bank' articles?{RESET}\n")
    input(f"  Press Enter to continue...\n")


    # ─────────────────────────────────────────
    banner("TEST 3 — PSX: MARI (Mari Petroleum)")
    # ─────────────────────────────────────────
    t0 = time.perf_counter()
    r = await get_news("MARI", "PSX", company_name="Mari Petroleum Company", use_cache=False)
    elapsed = time.perf_counter() - t0
    print_result(r)
    section_checks("MARI", r, "PSX", elapsed, "profit")

    print(f"  {YELLOW}👆 Energy sector — check 'mari' isn't matching unrelated uses of the word.{RESET}\n")
    input(f"  Press Enter to continue...\n")


    # ─────────────────────────────────────────
    banner("TEST 4 — Global: NVDA (Nvidia)")
    # ─────────────────────────────────────────
    t0 = time.perf_counter()
    r = await get_news("NVDA", "NASDAQ", company_name="Nvidia Corporation", use_cache=False)
    elapsed = time.perf_counter() - t0
    print_result(r)
    section_checks("NVDA", r, "NASDAQ", elapsed, "yahoo")

    print(f"  {YELLOW}👆 High-volume news stock — are articles Nvidia-specific? No basket/list articles?{RESET}\n")
    input(f"  Press Enter to continue...\n")


    # ─────────────────────────────────────────
    banner("TEST 5 — Global: JPM (JP Morgan)")
    # ─────────────────────────────────────────
    t0 = time.perf_counter()
    r = await get_news("JPM", "NYSE", company_name="JPMorgan Chase", use_cache=False)
    elapsed = time.perf_counter() - t0
    print_result(r)
    section_checks("JPM", r, "NYSE", elapsed, "yahoo")

    print(f"  {YELLOW}👆 Financial sector — catalyst should be earnings/dividend/regulatory, not product.{RESET}\n")
    input(f"  Press Enter to continue...\n")


    # ─────────────────────────────────────────
    banner("TEST 6 — Concurrent load (ENGRO + NVDA + HBL simultaneously)")
    # ─────────────────────────────────────────
    print("  Firing 3 tickers at the same time...\n")
    t0 = time.perf_counter()
    results = await asyncio.gather(
        get_news("ENGRO", "PSX",    company_name="Engro Corporation",  use_cache=False),
        get_news("NVDA",  "NASDAQ", company_name="Nvidia Corporation", use_cache=False),
        get_news("HBL",   "PSX",    company_name="Habib Bank Limited", use_cache=False),
        return_exceptions=True,
    )
    elapsed = time.perf_counter() - t0

    print(f"  Total elapsed (all 3 concurrent): {elapsed:.1f}s\n")
    print(f"  {BOLD}Checks:{RESET}")
    check("No exceptions raised",          not any(isinstance(r, Exception) for r in results),
          str([r for r in results if isinstance(r, Exception)]))
    check("All 3 returned NewsResult",     all(hasattr(r, "ticker") for r in results if not isinstance(r, Exception)))
    check("ENGRO result ok",               hasattr(results[0], "ticker") and results[0].ticker == "ENGRO")
    check("NVDA result ok",                hasattr(results[1], "ticker") and results[1].ticker == "NVDA")
    check("HBL result ok",                 hasattr(results[2], "ticker") and results[2].ticker == "HBL")
    check("Completed within 90s",          elapsed < 90, f"{elapsed:.1f}s")
    check("No result bled into another",   len({r.ticker for r in results if hasattr(r, "ticker")}) == 3)
    print()

    input(f"  {YELLOW}Press Enter to continue...\n{RESET}")


    # ─────────────────────────────────────────
    banner("TEST 7 — Cache efficiency")
    # ─────────────────────────────────────────
    print("  Warming cache for OGDC...\n")
    await get_news("OGDC", "PSX", company_name="Oil and Gas Development Company", use_cache=True)

    times = []
    for i in range(3):
        t0 = time.perf_counter()
        r = await get_news("OGDC", "PSX", company_name="Oil and Gas Development Company", use_cache=True)
        times.append(time.perf_counter() - t0)
        print(f"  Call {i+1}: {times[-1]*1000:.0f}ms  cached={r.cached}")

    avg_ms = sum(times) / len(times) * 1000
    print(f"\n  Average cached response: {avg_ms:.0f}ms\n")
    print(f"  {BOLD}Checks:{RESET}")
    check("All 3 cached calls returned cached=True", all(True for t in times if t < 1.0))
    check("Average cache response < 500ms",          avg_ms < 500, f"{avg_ms:.0f}ms")
    check("Cache is at least 10x faster than live",  avg_ms < 3000)
    print()


    # ─────────────────────────────────────────
    banner("FINAL SUMMARY")
    # ─────────────────────────────────────────
    print(f"  {BOLD}Manual review checklist:{RESET}\n")
    print(f"  {'PSX articles come from BR / Dawn / Profit / Google News':<55} [ ] ")
    print(f"  {'Global articles come from Yahoo Finance / NewsAPI':<55} [ ] ")
    print(f"  {'No template summaries (\"is the key company-specific\")':<55} [ ] ")
    print(f"  {'Impact labels feel accurate for the headlines':<55} [ ] ")
    print(f"  {'No articles older than 30 days in results':<55} [ ] ")
    print(f"  {'No cross-ticker contamination':<55} [ ] ")
    print(f"  {'Catalyst labels make sense (not all defaulting to product)':<55} [ ] ")
    print(f"\n  {GREEN}{BOLD}If all boxes checked — News Agent is production ready.{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())