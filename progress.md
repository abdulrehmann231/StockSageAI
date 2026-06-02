# StockSage AI — Build Progress

Living status of every phase in [`plan.md`](./plan.md) § 10. Updated as branches merge to `main`.

Legend: ✅ done · 🟡 partial / in progress · ⏳ not started

---

## Phase 0 — Setup ✅

Monorepo scaffolded; local dev infra runnable.

- Root: `.gitignore`, `README.md`, `docker-compose.yml` (Postgres 16 + Redis 7), `plan.md`
- `backend/`: FastAPI skeleton, async SQLAlchemy session, pydantic-settings config, package layout for `api / agents / workers / services / scrapers / db / core`, `run.py` Windows-aware entry point
- `frontend/`: Next.js 14 (App Router), Tailwind, TanStack Query, base `layout.tsx` / `page.tsx`
- `.env` templates for both sides

---

## Phase 1 — Auth + Stock Search ✅

Shipped on `feat/auth-and-stock-search` (PR #1, merged).

**Backend**
- SQLAlchemy `User` and `Stock` models, async Postgres session, `pgcrypto` extension auto-installed
- JWT auth (bcrypt password hash, HS256 token) issued as a **HttpOnly cookie** with bearer-fallback for tooling
- Endpoints: `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
- Endpoints: `GET /api/stocks`, `GET /api/stocks/search?q=`, `GET /api/stocks/{ticker}` — search escapes LIKE wildcards (`%`, `_`)
- Rate limiting via slowapi: 5/minute on `/signup` + `/login` (configurable via `AUTH_RATE_LIMIT`)
- 88-stock seed (44 PSX + 42 US/global), idempotent populate script

**Frontend**
- Auth context using HttpOnly cookies (`withCredentials: true`, no localStorage)
- `/login`, `/signup`, protected `/dashboard` with header + logout
- Fuzzy stock search (`fuse.js`) with ARIA combobox/listbox roles; search results link to detail pages

**Tests:** 27 backend pytest tests, all green.

**Bot review (PR #1) addressed:** wildcard injection, JWT-in-cookies, 401-only token clear, ARIA, rate limiting, `.env.test` extraction, Alembic note in README.

---

## Phase 2 — Price Agent MVP ✅

Completed with improvements from code review.

**Working**
- Backend `agents/price_agent.py` routes by market — `yfinance` for global, Playwright sync API for PSX (wrapped in `asyncio.to_thread`, with `WindowsProactorEventLoopPolicy` swap because psycopg-async uses the Selector loop elsewhere)
- Redis cache at `services/cache_service.py` with 60s TTL; tests use db 15 with autouse flush
- `GET /api/stocks/{ticker}/price` → `PriceQuote` JSON; 404 if unknown ticker, 502 if upstream fails
- Frontend stock detail page `/dashboard/stocks/[ticker]` with live `PriceCard` (TanStack Query, 30s refetch)
- Tests: 13 added (cache service, agent stubs, endpoint behaviour) — total backend suite now **40/40 green**

**Verified live**
- Global / yfinance: AAPL → price $300.23, +0.68%, full OHLC + 52w + market cap + P/E + EPS + dividend yield
- PSX / Playwright scrape: ENGRO → PKR 485.38, +1.48% with OHLC + volume

**Improvements completed (code review follow-ups)**

| Improvement | Status | Details |
|---|---|---|
| PSX 52w range fix | ✅ | Fixed parsing for high-priced tickers like NESTLE (now parses visible text instead of unreliable data attributes) |
| PSX selector logging | ✅ | Added comprehensive logging for selector failures to detect PSX website changes early |
| Browser pool | ✅ | Implemented `scrapers/browser_pool.py` - reuses Chromium instance across requests (~1-2s savings per request) |
| Browser pool threading fix | ✅ | Rewrote `BrowserPool` around a pinned `ThreadPoolExecutor(max_workers=1)` so every Playwright call lands on the same thread — eliminates the intermittent `greenlet.error: Cannot switch to a different thread` that fired under sequential and concurrent PSX scrapes. Verified across 3 sequential + 1 concurrent (`asyncio.gather`, 5 tickers) run with zero greenlet errors. |
| Integration test markers | ✅ | Added `@pytest.mark.live` and `@pytest.mark.slow` markers in pytest.ini for gating network tests |
| PSX scraper tests | ✅ | Added `tests/test_psx_scraper.py` with unit tests for parsing helpers + integration tests for live scraping |

**PSX data coverage (tested across 8 tickers: ENGRO, HBL, OGDC, LUCK, NESTLE, SYS, FFC, MEBL)**

| Field | Coverage | Notes |
|---|---|---|
| price | ✅ 8/8 | always works |
| change / change_pct | ✅ 8/8 | always works |
| previous_close | ✅ 8/8 | derived from `price − change` |
| 52w high / low | ✅ 8/8 | **FIXED** - now parses text correctly for all price ranges |
| open, day_high, day_low, volume | ✅ 8/8 | Now cached for 24h - uses last known values after market close |
| market_cap | ✅ 8/8 | Extracted from Equity section (in PKR thousands, converted to actual) |
| pe_ratio | ✅ 8/8 | Extracted from stats section |
| eps | ✅ 8/8 | Extracted from Financials section |
| total_shares | ✅ 8/8 | Extracted from Equity section |
| free_float_shares | ✅ 8/8 | Extracted from Equity section |
| free_float_pct | ✅ 8/8 | Extracted from Equity section |
| net_profit_margin | partial | Extracted when available |
| dividend_yield | partial | Extracted when available on page |

**Complete Data Features**
- Long-term OHLC cache (24h) - fills in missing intraday data after PSX market close
- Extracts data from multiple page sections (Quote, Equity, Financials)
- Market cap converted to actual PKR value (PSX shows in thousands)
- Additional fields: total_shares, free_float_shares, free_float_pct, net_profit_margin

---

## Medium Priority Improvements ✅

Completed infrastructure improvements from code review:

### API Pagination ✅
- `GET /api/stocks` now returns paginated results with `PaginatedStocks` schema
- Includes `meta` object with: `total`, `page`, `per_page`, `total_pages`, `has_next`, `has_prev`
- Query params: `page` (1-indexed), `per_page` (default 50, max 500), `market` filter

### Structured Logging with Request IDs ✅
- New `core/logging.py` module with:
  - `RequestIdFilter` - injects request ID into all log records via context variable
  - `JSONFormatter` - structured JSON logs for production (timestamps, levels, exception traces)
  - `DevFormatter` - colored, human-readable logs for development
  - `setup_logging()` - configures logging based on `DEBUG` env var
- New `core/middleware.py` with `RequestIdMiddleware`:
  - Generates unique request ID per request (or uses `X-Request-ID` header if provided)
  - Logs request start/completion with method, path, status, duration_ms
  - Adds `X-Request-ID` to response headers for client correlation
- Context variable (`request_id_ctx`) propagates ID through async code

### Health Check Endpoints ✅
- `GET /health` - basic health check (always returns `{"status": "healthy"}`)
- `GET /health/ready` - readiness check that verifies:
  - Database connectivity (runs `SELECT 1`)
  - Redis connectivity (runs `PING`)
  - Returns 503 with detailed status if any dependency is unhealthy

### Browser Pool for Playwright ✅
- `scrapers/browser_pool.py` module:
  - Singleton `BrowserPool` owning a `ThreadPoolExecutor(max_workers=1)` — every Playwright operation (launch, `new_context`, page work, teardown) runs on the same pinned thread, so greenlet state never has to switch threads.
  - Reuses single Chromium browser instance across requests; creates fresh contexts per call for isolation.
  - Automatic cleanup on application shutdown (`atexit` + lifespan hook).
  - Public API: `run_with_page(fn, *, user_agent=None)` (blocking) and `run_with_page_async(fn, *, user_agent=None)` (async via `loop.run_in_executor`). The earlier `get_page` / `get_page_sync` context-manager API was removed — it could not safely return a `page` across threads under the sync-greenlet model.
- PSX scraper (`scrapers/psx_prices.py`) calls `pool.run_with_page_async(...)` directly from `fetch_psx_quote` instead of wrapping the pool in `asyncio.to_thread`; the `use_pool=False` path is preserved as `_fetch_sync_no_pool` for one-shot callers.
- Tradeoff: PSX scrapes are now **serialized** through the single pinned thread. Acceptable while the 60s short-term cache + 24h OHLC cache absorb most repeat traffic; would need a pool-of-pools for true parallel PSX scraping later.
- Estimated performance improvement vs no pool: ~1-2s per PSX request (Chromium reuse).

---

## Phase 3 — News + Sentiment Agents 🟡

Per plan § 4.5 / § 4.7: scrape Business Recorder / Dawn / Profit Pakistan for PSX, NewsAPI + Yahoo Finance feed for global, then build Reddit + StockTwits sentiment ingestion.

### News Agent ✅

- Implemented `backend/agents/news_agent.py` with a LangGraph-compatible `news_agent(state)` node wrapper and local CLI testing support.
- Added multi-source news fetching:
  - PSX: Business Recorder, Dawn Business, Profit Pakistan, Google News.
  - Global: Yahoo Finance, NewsAPI, Google News.
- Added dedicated scraper modules under `backend/scrapers/` plus shared article extraction and normalization helpers.
- Added LLM-based relevance, summary, impact, and catalyst analysis using OpenRouter/Gemini-compatible chat completion flow.
- Added deterministic fallback logic for cases where the LLM is unavailable or source text is weak.
- Supports plan-compatible impact labels: `HIGH_POSITIVE`, `MEDIUM_POSITIVE`, `NEUTRAL`, `MEDIUM_NEGATIVE`, `HIGH_NEGATIVE`.
- Supports plan-compatible catalysts: `earnings`, `dividend`, `M&A`, `regulatory`, `executive_change`, `product`, `lawsuit`.
- Added recency filtering, near-duplicate removal, source failure isolation, two-sentence summary cleanup, and article relevance safeguards.
- Added Redis cache support with 30-minute TTL; cache failures do not block fresh news results.
- Added `backend/test2.py` for local real-world testing across PSX and global tickers.

### Sentiment Agent ✅

- Implemented `backend/agents/sentiment_agent.py` with a LangGraph-compatible `sentiment_agent(state)` node wrapper (populates `sentiment_data`) plus a local CLI tester (`python -m agents.sentiment_agent AAPL [MARKET] [--company ...] [--no-cache]`).
- Multi-source gathering with per-source failure isolation:
  - Global: Reddit over r/stocks, r/investing, r/wallstreetbets, r/StockMarket + StockTwits free public symbol stream + best-effort X/Twitter.
  - PSX: Reddit over r/PakistaniInvestors, r/pakistan + scraped public Telegram channels + best-effort X/Twitter. (StockTwits rarely carries PSX symbols, so it is omitted from PSX routing.)
  - Scrapers: `backend/scrapers/reddit_sentiment.py`, `backend/scrapers/stocktwits_sentiment.py`, `backend/scrapers/telegram_sentiment.py`, `backend/scrapers/x_sentiment.py`.

**Sentiment Agent — completion (Telegram + X + credential-free Reddit + REST):**

- **Reddit is now credential-free.** `reddit_sentiment.py` uses PRAW when `REDDIT_CLIENT_ID`/`SECRET` are set, but otherwise scrapes Reddit's public search JSON (`reddit.com/r/<sub>/search.json`) so the source works with zero setup. Throttling (403/429) degrades to empty.
- **Telegram (PSX) via public web preview — no Telethon, no credentials.** `telegram_sentiment.py` scrapes `https://t.me/s/<channel>` preview pages with httpx + BeautifulSoup, keeps messages mentioning the ticker/company, and normalizes them (text, date, views→`score`, url). Channels configurable via `PSX_TELEGRAM_CHANNELS`; unknown/private channels just 404 and are skipped.
- **X/Twitter best-effort.** `x_sentiment.py` prefers the official recent-search API when `X_BEARER_TOKEN` is set, otherwise tries public Nitter mirrors (`NITTER_INSTANCES`), otherwise returns []. *Always* degrades to empty rather than raising — X is the least reliable source by design.
- All new sources are credential-free and isolated: a dead channel, dead Nitter instance, or rate-limit never sinks the agent.
- **REST endpoint:** `GET /api/sentiment/{ticker}` (`backend/api/sentiment.py`, registered in `main.py`) — resolves market/company from the stocks table, runs the agent, returns the scored `SentimentResult`. `?refresh=true` bypasses the 2h Redis cache. 404 for unknown ticker, 502 on agent failure.
- LLM scoring via OpenRouter (`llm_service.analyze_sentiment_posts`) returns `overall_sentiment` (−1..+1), `bullish_pct`/`bearish_pct`, and top bullish/bearish points. Output is validated/clamped/renormalized (`_coerce_llm_scores`) so bad model output can't poison the pipeline.
- Deterministic fallback (`_deterministic_score`) scores posts by keyword lexicon + provider labels (StockTwits Bullish/Bearish tag wins) when the LLM is unavailable or returns an unusable payload. Points are backfilled from real posts if the LLM omits them.
- Result shape matches plan § 4.7: `overall_sentiment`, `bullish_pct`, `bearish_pct`, `top_bullish_points`, `top_bearish_points`, `post_count` (plus `label`, `sources`, `errors`, `fetched_at`, `cached` for consistency with the News Agent).
- Redis cache with 2h TTL (prefix `sentiment:`), keyed by market+ticker; cache failures never block a fresh fetch.
- Reddit credentials are optional — the agent simply skips Reddit when `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` are unset. Added `reddit_*` fields to `core/config.py` and model-override docs to `.env.example`.
- **Tests:** 81 offline tests, all green (run with `-m "not live"`):
  - `test_sentiment_agent.py` — classification, deterministic scoring, LLM-payload validation/clamping/renormalization, source aggregation, caching, `use_cache=False` bypass, empty-posts neutral path, source-failure isolation, PSX vs GLOBAL source routing (now including Telegram + X), X-failure isolation, dedup, ticker normalization, node wrapper.
  - `test_telegram_sentiment.py` — view parsing, mention matching (word-boundary, no substring false-positives), HTML parse/normalize, channel routing from env, stubbed-fetch happy path + failure tolerance.
  - `test_x_sentiment.py` — query building, Nitter HTML parse (incl. real `May 30, 2026 · 3:00 PM UTC` date format), official-API-vs-Nitter routing, and the "all sources fail → []" degradation contract.
  - `test_reddit_sentiment.py` — public-JSON normalize/dedup/throttle handling and PRAW-vs-public-JSON routing.
  - `test_sentiment_api.py` — endpoint contract: scored result, ticker-case normalization, `refresh` → `use_cache=False`, 404, 502.
  - Plus `@pytest.mark.live` tests (StockTwits, Telegram preview) deselected by default.

### News Agent Notes

- Global sources can return many articles; the agent intentionally discards low-quality candidates instead of forcing five weak articles into the report.
- Scraping is generally working; when global results are low, it is usually because relevance/quality filters rejected articles, not because sources returned nothing.
- Redis cache behavior still needs to be tested with Redis running locally or in Docker.

### Remaining In Phase 3

- ~~Implement Sentiment Agent per plan § 4.7 using Reddit + StockTwits sentiment sources.~~ ✅ Done (see Sentiment Agent section above).
- ~~PSX sentiment beyond Reddit — Telegram / X scraping per plan § 4.7.~~ ✅ Done via credential-free web scraping (public `t.me/s/` Telegram previews + Nitter/official X), no Telethon required.
- ~~Wire the Sentiment Agent into an API endpoint.~~ ✅ Done — `GET /api/sentiment/{ticker}`.
- Wire the **News** agent into an API endpoint (still pending; sentiment is now exposed, news is not).
- Verify News + Sentiment agents together inside the orchestrator/LangGraph flow (Phase 5).
- Re-test Redis cache speed once Redis is running.
---

## Phase 4 — Filings RAG Agent ⏳

Not started. Per plan § 4.6: SEC EDGAR + PSX annual reports → Pinecone embeddings → grounded Q&A. **Natural home for remaining PSX fundamentals (detailed market cap, EPS history, dividend payouts).**

---

## Phase 5 — Orchestration + Report Writer ⏳

Not started. LangGraph multi-agent fan-out/fan-in + Claude Sonnet report writer.

---

## Phase 6 — Chat + Watchlist + Alerts ⏳

Not started. Includes chat-with-stock, watchlist CRUD, Celery alert engine.

---

## Phase 7 — Portfolio Tracker ⏳

Not started.

---

## Phase 8 — Polish + Macro + Daily Briefings ⏳

Not started.

---

## Cross-cutting follow-ups (tracked here so they don't get lost)

- [ ] Migrate from `Base.metadata.create_all` on startup to Alembic migrations before prod (noted in README).
- [x] PSX scraper resilience: log selector failures so we get early warning when PSX redesigns.
- [x] PSX 52w high/low correctness for high-priced tickers (see Phase 2 gaps).
- [ ] PSX fundamentals (market cap / P/E / EPS / div yield) via Phase 4 RAG.
- [x] Add an integration-test marker (`@pytest.mark.live`) and gate the live PSX/yfinance hits behind it so CI doesn't depend on external network.
- [x] Add API pagination for stocks endpoint.
- [x] Add structured logging with request IDs.
- [x] Add health check endpoints for Redis/DB connectivity.
- [x] Implement browser pool for Playwright to reuse browser instances.
- [x] Fix browser-pool threading bug (pinned-thread model — see Phase 2 improvements table).
- [x] **Delisted-ticker detection** — `PriceQuote` now carries `is_delisted: bool` and `data_as_of: date | None`. PSX scraper parses the `DELISTED` badge text (case-sensitive uppercase substring to dodge the lowercase "delisted from the Exchange" disclaimer paragraph that appears on every PSX page) and the "As of &lt;Day&gt;, &lt;Month&gt; &lt;day&gt;, &lt;year&gt;" timestamp. Verified: ENGRO → `is_delisted=True, data_as_of=2025-01-03`; MARI/LUCK → `False, <today>`. Global path leaves the defaults (False / None).
- [x] **yfinance dividend yield normalization** — investigated and resolved: not a bug. yfinance 1.3.0's `info["dividendYield"]` is already in percent units (AAPL → 0.36, KO → 2.61, VZ → 6.05 all match reality). The fractional variant lives in `trailingAnnualDividendYield`, which the agent never reads. NVDA's `+0.02%` was flagged on a hunch but is actually correct (Nvidia pays ~$0.04/share annually on a ~$222 price). No code change needed; closing the follow-up so we don't add defensive normalization for a problem that doesn't exist.
- [ ] Add React error boundary in frontend.
- [ ] Add offline/network error handling in frontend.
