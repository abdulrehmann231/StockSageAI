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
- New `scrapers/browser_pool.py` module:
  - Singleton `BrowserPool` class with thread-safe lazy initialization
  - Reuses single Chromium browser instance across requests
  - Creates fresh contexts per request for isolation
  - Automatic cleanup on application shutdown
  - `get_page()` async context manager for easy usage
  - `get_page_sync()` sync context manager for thread pool usage
- PSX scraper updated to use pool by default (configurable via `use_pool` param)
- Estimated performance improvement: ~1-2s per PSX request

---

## Phase 3 — News + Sentiment Agents ⏳

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

### News Agent Notes

- Global sources can return many weak/paywalled/thin articles; the agent intentionally discards low-quality candidates instead of forcing five weak articles into the report.
- Scraping is generally working; when global results are low, it is usually because relevance/quality filters rejected articles, not because sources returned nothing.
- Redis cache behavior still needs to be tested with Redis running locally or in Docker.

### Remaining In Phase 3

- Implement Sentiment Agent per plan § 4.7 using Reddit + StockTwits sentiment sources.
- Run full phase-level integration test after Sentiment Agent is complete.
- Verify News + Sentiment agents together inside the orchestrator/LangGraph flow.
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
- [ ] Add React error boundary in frontend.
- [ ] Add offline/network error handling in frontend.
