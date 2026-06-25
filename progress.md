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

## Phase 3 — News + Sentiment Agents ✅

Per plan § 4.5 / § 4.7: scrape Business Recorder / Dawn / Profit Pakistan for PSX, NewsAPI + Yahoo Finance feed for global, then build Reddit + StockTwits sentiment ingestion. Both agents are implemented, exposed over REST, and Redis-cached. Stitching them into a unified report is the Phase 5 orchestrator's job (deferred by design).

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
- **REST endpoint:** `GET /api/news/{ticker}` (`backend/api/news.py`, registered in `main.py`) — resolves market/company from the stocks table, returns ranked impact-classified articles. `?refresh=true` bypasses the 30m cache; `?limit=` (1–20) caps article count. 404 unknown ticker, 502 on agent failure. Tests in `backend/tests/test_news_api.py` (6, offline/stubbed).

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

### Phase 3 — closed out

- ~~Implement Sentiment Agent per plan § 4.7 using Reddit + StockTwits sentiment sources.~~ ✅ Done (see Sentiment Agent section above).
- ~~PSX sentiment beyond Reddit — Telegram / X scraping per plan § 4.7.~~ ✅ Done via credential-free web scraping (public `t.me/s/` Telegram previews + Nitter/official X), no Telethon required.
- ~~Wire the Sentiment Agent into an API endpoint.~~ ✅ Done — `GET /api/sentiment/{ticker}`.
- ~~Wire the News agent into an API endpoint.~~ ✅ Done — `GET /api/news/{ticker}`.
- ~~Re-test Redis cache speed once Redis is running.~~ ✅ Verified end-to-end against a live Redis: first call hits sources, second call served from cache with zero source fetches (both News 30m + Sentiment 2h TTLs).

**Deferred to Phase 5 (by design, not a Phase 3 gap):** stitching News + Sentiment into a single report via the LangGraph orchestrator + Report Writer.

> Note: two unrelated **pre-existing** test failures (stale after earlier refactors) were also fixed so the full suite runs clean — `tests/test_psx_scraper.py` imported the renamed `_read_52w_range`/`_read_stats` helpers (now `_read_all_stats` + `_extract_52w_range`), and `tests/test_stocks.py` still expected a bare list from `/api/stocks` after that endpoint moved to a paginated `{items, meta}` shape. **Full backend suite: 153 passed, 8 deselected (live).**
---

## Phase 4 — Filings RAG Agent ✅ (backend)

Per plan § 4.6: SEC EDGAR (US) + PSX disclosures → embeddings → **pgvector** → grounded Q&A with citations. Built on a **free + deployable** stack — **Gemini** embeddings + **pgvector** (Supabase-native), no Pinecone, no OpenAI key, no PyTorch in the image.

### Embedding service (`backend/services/embedding_service.py`)

- Gemini `text-embedding-004` (768-dim) via its **OpenAI-compatible endpoint** — reuses the existing `AsyncOpenAI` client, no new SDK.
- **Two-key rotation:** reads `GEMINI_API_KEY` + `GEMINI_API_KEY_2` (and optional `GEMINI_API_KEYS`), round-robins per batch and fails over on error → ~2× the free-tier rate limit for the bulk indexing pass.
- **Deterministic offline fallback:** with no key configured, returns a hashing bag-of-words vector (L2-normalised) so retrieval is still meaningful in CI/tests and the pipeline never hard-fails. Verified: shared-vocabulary texts score higher cosine similarity than unrelated ones.

### Vector store (`backend/db/models.py` + `backend/services/filings_store.py`)

- New `Filing` (idempotent on `(ticker, source, external_id)`) and `FilingChunk` (with a `Vector(768)` column) models. `CREATE EXTENSION vector` is run in the app lifespan + test conftest; pgvector built from source locally to match Supabase prod.
- `upsert_filing` / `replace_chunks` (re-index replaces, never duplicates — verified) / `search` (pgvector **cosine distance**, `1 - distance` similarity since embeddings are unit-norm) / `filing_status`.

### Ingestion (`backend/scrapers/` + `backend/services/filings_index.py`)

- `sec_edgar.py` — SEC's **free** JSON API (no key, just a descriptive User-Agent): `company_tickers.json` → CIK, `submissions` → latest 10-K/10-Q, fetch primary doc, strip HTML → text (capped at 40k words). **Live-verified** against the real API (fetched a current AAPL 10-Q, 10k+ words). Degrades to `[]` on any failure.
- `psx_filings.py` — best-effort PSX company-disclosure scraper; returns `[]` gracefully when nothing parseable (PSX has no clean text API — flagged in the plan as "the hard part"). The pipeline/RAG runtime are source-agnostic, so PSX coverage improves purely by strengthening this fetcher.
- `filings_common.py` — `clean_text` + overlapping word-window `chunk_text` (~750 words / 150 overlap ≈ plan's 1000-token/200-overlap).
- `filings_index.index_ticker` — fetch → chunk → embed → upsert, returns a per-filing summary; callable from the API or a future Celery refresh job.

### RAG agent (`backend/agents/filings_agent.py`)

- `answer_question` — embed query → pgvector top-k → grounded LLM answer **with `[10-K FY2025]`-style citations** via `llm_service.answer_from_filings`. The LLM is instructed to answer only from the excerpts. **Extractive fallback** (quote the most-relevant chunk) when no LLM key is set, so it works offline. Clean "nothing indexed yet" path.
- `auto_analysis` — the plan's five auto questions (revenue trend, profit margin, debt level, risks, outlook) compiled into a structured `FilingsData`.
- `llm_service.answer_from_filings` + `_resolve_chat_provider`: prefers OpenRouter when set, **else uses Gemini directly** (`gemini-2.0-flash`) — so the two Gemini keys also power the LLM with no OpenRouter account needed.

### REST endpoints (`backend/api/filings.py`, registered in `main.py`)

All auth-gated; ticker/market resolved from the stocks table to route SEC vs PSX:

- `POST /api/filings/{ticker}/index` (`{limit}`) — fetch + index.
- `GET  /api/filings/{ticker}/status` — indexed filing/chunk counts.
- `POST /api/filings/{ticker}/ask` (`{question, k}`) — grounded Q&A.
- `GET  /api/filings/{ticker}/analysis` — the five key-question answers.

### Tests

- **15 new offline tests** (`test_filings_rag.py`): chunker overlap/empty, clean_text, embedding determinism + shared-vocab ranking, pgvector upsert/search ranking, re-index-replaces-not-duplicates, index pipeline (stubbed SEC), no-documents path, PSX routing, RAG no-index/extractive-fallback paths + citation tags, and the API contract (auth, index→status→ask, 404).
- **Full backend suite: 302 passed, 8 deselected (live).** No regressions in the prior 287.

### Phase 4 — closed out (backend)

- ~~SEC EDGAR ingestion + chunking + embedding + vector upsert.~~ ✅ (free API, live-verified.)
- ~~Vector store + similarity retrieval.~~ ✅ (pgvector cosine.)
- ~~RAG runtime: grounded answers with citations + five auto questions.~~ ✅
- ~~REST endpoints.~~ ✅

**Deferred (intentional):** richer SEC text extraction (strip leading iXBRL/XBRL fact noise from newer primary docs), robust PSX PDF extraction (`pypdf` + LLM cleanup per plan), Celery weekly re-index job, frontend filings panel, and migrating off auto-`create_all` to Alembic before prod.

---

## Phase 5 — Orchestration + Report Writer ✅

Per plan § 4.8 / § 5: parallel fan-out of the Price + News + Sentiment agents into a synthesizing Report Writer that produces a single analyst-style `StockReport`.

### Orchestrator

- `backend/agents/orchestrator.py` runs `price_agent.get_price`, `news_agent.get_news`, and `sentiment_agent.get_sentiment` concurrently via `asyncio.gather(..., return_exceptions=True)`.
- **Per-agent error isolation:** one agent crashing does not sink the report — the failure goes into `StockReport.errors`, that channel's payload is set to `None`, and the writer is happy with any combination of present/absent inputs (verified by `test_orchestrator_isolates_single_agent_failure` and the all-three-fail variant).
- **Verified parallel:** `test_orchestrator_runs_agents_in_parallel` stubs each agent with a 200ms sleep and asserts total < 450ms (serial would be ~600ms+).
- Redis cache with 30m TTL keyed by `report:{market}:{ticker}`. Cache read/write failures never block a fresh run (`test_orchestrator_cache_read_failure_does_not_block` / `..._write_...`); `?refresh=true` bypasses both directions.
- LangGraph-compatible `report_orchestrator(state)` node wrapper populates `report_data` plus per-agent `price_data`/`news_data`/`sentiment_data` keys so the orchestrator can be dropped into a larger graph later.
- We deliberately used `asyncio.gather` instead of a full LangGraph `StateGraph` because the topology here is one fan-out + one fan-in with no conditional edges or loops. The graph would have changed nothing observable about the result while making testing harder.

### Report Writer

- `backend/agents/report_writer.py` produces a `StockReport` pydantic model with: verdict (`BUY` / `ACCUMULATE` / `HOLD` / `REDUCE` / `SELL`), confidence (`low`/`medium`/`high`), composite signal score in `[-1, +1]`, executive summary, per-channel narrative sections, key catalysts, risks, opportunities, the raw agent payloads, sources, errors, `model_used`, and `cached`/`fetched_at` metadata.
- **Composite score** is a weighted blend: News 0.45 (averaged across articles, recency-weighted), Sentiment 0.35 (clamped to `[-1, 1]`), Price 0.20 (% change clamped to ±10%). Total contributing weight determines confidence — only when all three channels participate do we surface `high`.
- **Verdict thresholds:** `≥ 0.55` → BUY, `≥ 0.20` → ACCUMULATE, `≤ -0.20` → REDUCE, `≤ -0.55` → SELL, otherwise HOLD.
- **LLM synthesis** via `llm_service.synthesize_report` (new) using the `report_agent` model chain (`REPORT_AGENT_MODEL` env, falls back to the news chain). The LLM receives a condensed JSON payload of the three signals plus the deterministic suggested verdict, returns verdict + confidence + narrative + risks/opportunities. Output is validated, clamped, alias-coerced (e.g. `"strong buy"` → `BUY`, `"outperform"` → `ACCUMULATE`), capped at 4 items per list, and falls back to deterministic when the model is missing the executive summary, returns non-dict, or no API key is configured.
- **Deterministic path** (always available) produces verdict + summary + sections + risks/opps directly from agent payloads. Concrete-first ordering for risks/opps — a 5% drawdown or `DELISTED` flag outranks the third negative-news article in the list.

### REST endpoint

- `GET /api/report/{ticker}` (`backend/api/report.py`, registered in `main.py`) — resolves market/company from the stocks table, runs the orchestrator, returns the `StockReport`. `?refresh=true` bypasses the cache; `?max_news_articles=` (1–20) caps how many articles the News Agent considers. 404 for unknown tickers, 502 on orchestrator failure. Tests in `backend/tests/test_report_api.py` cover all six contract points.

### Tests

- **47 new tests across 3 files** (`test_report_writer.py`, `test_orchestrator.py`, `test_report_api.py`); all offline/stubbed.
- **Full suite: 200 passed, 8 deselected (live).** No regressions in the prior 153 tests.
- **Also fixed a latent bug in the PR #12 PSX retry path:** `_read_all_stats` now *merges* the retry attempt into the first-attempt dict instead of overwriting. The previous overwrite path would have lost parsed stats whenever the retry returned fewer items (test mocks consumed the locator state; in production, a transient render glitch on the second pass). Caught by `test_psx_scraper.py::TestRead52wRange`.

### Phase 5 — closed out

- ~~Parallel fan-out of Price + News + Sentiment agents.~~ ✅ (`asyncio.gather` with per-agent isolation.)
- ~~Report Writer agent producing a single structured report.~~ ✅ (`StockReport` with verdict, confidence, narrative sections, risks/opps.)
- ~~LLM-driven synthesis with deterministic fallback.~~ ✅ (`llm_service.synthesize_report` + always-on deterministic path.)
- ~~REST endpoint to surface the report.~~ ✅ (`GET /api/report/{ticker}`.)
- ~~Redis caching with refresh override.~~ ✅ (30m TTL keyed by market+ticker.)

> Note: Claude Sonnet remains the planned production model for the report writer, but the OpenRouter client already supports any model behind that gateway — switch via the `REPORT_AGENT_MODEL` env without touching code.

---

## Phase 6 — Chat + Watchlist + Alerts ✅

Per plan § 4.9–4.11 / § 6: persistent Reports, chat-with-stock follow-up Q&A, watchlist CRUD, and a user-owned alert engine. All four surfaces ship behind the existing auth cookie + bearer fallback.

### DB schema (plan § 6)

New SQLAlchemy models in `backend/db/models.py`:

- `Report(id, user_id, ticker, market, verdict, confidence, composite_score, report_data JSONB, created_at)` — persisted Phase-5 `StockReport`. Indexed on `(user_id, created_at)` and `(ticker, created_at)`.
- `ChatMessage(id, report_id, role, content, created_at)` with CHECK constraint `role IN ('user','assistant')`.
- `WatchlistItem(user_id, ticker, added_at)` composite PK.
- `Alert(id, user_id, ticker, alert_type, condition JSONB, is_active, last_triggered, cooldown_hours, created_at)` with CHECK constraint on `alert_type`.

All FKs cascade on user/stock delete so `TRUNCATE TABLE users, stocks CASCADE` (already in `conftest.py`) cleans test rows automatically.

### Watchlist (plan § 4.10)

- `GET /api/watchlist` — current user's tracked stocks, ordered most-recently-added first, joined to `Stock` for enriched mini-card fields.
- `POST /api/watchlist` — **idempotent** (re-adding returns 200 with the existing row rather than a confusing 409); 404 for unknown tickers.
- `DELETE /api/watchlist/{ticker}` — 204 on success, 404 when the row isn't on the user's list.
- All three require auth; verified user-scoped (Bob can't see Alice's list).

### Reports persistence (plan § 4.9 / § 7)

Layered on top of the Phase-5 orchestrator:

- `POST /api/reports/generate` — runs `orchestrator.get_report`, persists the full `StockReport.model_dump` payload as a `Report` row owned by the current user.
- `GET /api/reports/user` — slim list view (no `report_data` blob) most-recent-first, capped at `limit` (1–100, default 20).
- `GET /api/reports/{id}` — full detail; user-scoped (other users get **404**, not 403, to avoid existence-leak side-channel).

### Chat-with-stock (plan § 4.9)

`backend/api/chat.py`:

- `POST /api/chat/{report_id}/message` — appends the user turn, generates the assistant reply, returns both.
- `GET /api/chat/{report_id}/history` — chronological history of both turns.
- **Two-track answering:** if `OPENROUTER_API_KEY` is configured, calls `llm_service.answer_chat_question` (new) with the persisted report payload + last 10 history turns as context. If the LLM returns `None` (no key, network error, empty content), falls back to a **deterministic data lookup** — `"What's the P/E?"` → `"P/E: 28.50."` straight from `report.price.pe_ratio`, no LLM call needed. The deterministic path keeps the feature usable offline + zero-cost for technical questions.
- **History ordering fix:** Postgres' `now()` is transaction-scoped — Q + A inserted in the same commit would share `created_at` and the UUID `id` tiebreaker is random, making turn order non-deterministic for fast back-to-back posts. Both messages are now stamped from Python at insert time so history reads back in the correct order. Verified by `test_chat_persists_history_and_passes_prior_turns`.

### Alerts (plan § 4.11)

**CRUD** (`backend/api/alerts.py`):
- `GET /api/alerts`, `POST /api/alerts`, `PATCH /api/alerts/{id}`, `DELETE /api/alerts/{id}`.
- `condition` is validated against `alert_type` at create + patch time (e.g. `PRICE_DROP` requires `threshold_pct < 0`; `PRICE_TARGET` requires `target > 0` + `direction ∈ {above, below}`); malformed conditions return 422 rather than being stored.

**Engine** (`backend/workers/alert_engine.py`):
- Five evaluators — `PRICE_DROP`, `PRICE_RISE`, `PRICE_TARGET`, `BIG_NEWS`, `SENTIMENT_SHIFT` — each a pure `(condition, signal) -> (fired, message, details)` function for trivially-offline unit tests.
- `run_alert_engine(db)` sweep:
  - Loads every active alert in one query.
  - **Cooldown gate runs BEFORE agent calls** — `last_triggered + cooldown_hours > now` short-circuits the alert before any Price/News/Sentiment fetch, so a cooled-down alert costs zero upstream calls per sweep.
  - Groups remaining alerts by ticker so Price/News/Sentiment are fetched at most once per ticker per sweep — and only the agents whose results are actually needed.
  - Routes each alert to its evaluator, fires events through the notifier, stamps `last_triggered` so the cooldown sticks across sweeps.
  - **State-machine for `SENTIMENT_SHIFT`:** the engine records the latest observed label into `condition._last_seen_label` on every sweep (even when the alert didn't fire) so a `from: bullish, to: bearish` trigger can detect the transition on the second sweep without needing a separate column. Verified by `test_run_alert_engine_sentiment_shift_state_machine`.
  - **Failure isolation:** a failing agent fetch, a failing evaluator, or a failing notifier each get captured in `result.errors` without taking the sweep down.

**Notifier abstraction** (`backend/services/notifier_service.py`):
- `Notifier` protocol with one async `send(event)` method.
- Ships with `LogNotifier` (default — structured INFO log + in-memory ring buffer for visibility), and `set_default_notifier(...)` for tests and an eventual email/push impl.
- Wiring the engine into Celery beat is a deployment concern, not an engine concern — `run_alert_engine` is callable directly from a regular async context and is already covered by `test_alert_engine.py`.

### Tests

- **60 new offline tests** across `test_watchlist.py` (7), `test_reports_api.py` (7), `test_chat_api.py` (9), `test_alerts_api.py` (14 incl. parametrised invalid-condition cases), `test_alert_engine.py` (23 mixing pure evaluators + end-to-end sweeps with a stubbed notifier).
- Coverage highlights: auth gating + user-scoping for every CRUD surface; the deterministic chat path + the LLM-on/off branching + history ordering; condition validation per alert type; cooldown gating; `SENTIMENT_SHIFT` state machine; agent-failure isolation; notifier-failure isolation; `LogNotifier` ring-buffer cap.
- **Full backend suite: 260 passed, 8 deselected (live).** No regressions in the prior 200 tests.

### Phase 6 — closed out

- ~~Chat with stock follow-up Q&A.~~ ✅ (`POST /api/chat/{report_id}/message` + `GET .../history`.)
- ~~Watchlist CRUD.~~ ✅ (idempotent POST, user-scoped, enriched list view.)
- ~~Alert engine + Celery scheduling.~~ ✅ engine itself shipped + tested. Beat-loop wiring is deployment glue; engine is callable directly today.
- ~~Persist Report rows so chat can ground replies without re-running agents.~~ ✅ (`POST /api/reports/generate` → DB; chat reads `report_data` JSONB.)

**Deferred (intentional):**
- Daily briefings cron / email delivery (Resend/SendGrid integration) — `Notifier` interface is the slot for it; switching `LogNotifier → EmailNotifier` is a one-line change.
- Celery beat scheduling specifics (`beat_schedule` config, worker container) — operational, not application-level.
- Frontend pages (`/watchlist`, `/alerts`, chat panel) — backend-only PR; the API contract is set.

---

## Phase 7 — Portfolio Tracker ✅ (backend)

Per plan § 4.15: manual holdings, live P&L, portfolio-wide metrics, transaction
history + CSV export, daily snapshots → performance chart, the 6th agent
(Portfolio Analyst), and capital-gains tax estimation. Backend-only PR; the API
contract is set for the frontend pages.

### DB schema (plan § 4.15)

New SQLAlchemy models in `backend/db/models.py`, all FK-cascaded off the user:

- `Holding(id, user_id, ticker, quantity, avg_buy_price, buy_date, notes, is_active, created_at, updated_at)` — CHECK `quantity > 0` and `avg_buy_price > 0`. Multiple lots of the same stock allowed; `is_active=False` retires a sold lot from live P&L while keeping it for history.
- `Transaction(id, user_id, holding_id, ticker, transaction_type, quantity, price, transaction_date, fees, notes, created_at)` — CHECK `transaction_type IN ('BUY','SELL')`. `holding_id` is `ON DELETE SET NULL` so deleting a holding preserves its transaction trail.
- `PortfolioSnapshot(id, user_id, total_value, total_cost_basis, total_gain_loss, snapshot_date, breakdown JSONB)` — UNIQUE `(user_id, snapshot_date)` so the daily worker upserts.
- `PortfolioAnalysis(id, user_id, health_score, analysis_data JSONB, recommendations JSONB, created_at)` — CHECK `health_score BETWEEN 0 AND 100`.

### Portfolio service (`backend/services/portfolio_service.py`)

Deterministic, DB-session-free math (the API hands it `(Holding, Stock)` rows):

- **Live P&L enrichment** — fetches one price per distinct ticker concurrently via the Price Agent with per-ticker failure isolation (a dead ticker becomes `price_error` on that holding, never sinks the view). Per holding: `cost_basis`, `current_value`, `gain_loss`, `gain_loss_pct`, `is_delisted`.
- **Aggregate metrics** (`compute_metrics`, pure) — total value / cost / gain-loss (%), best & worst performer, sector allocation %, market split % (PSX vs Global). Unpriced holdings are excluded from value totals but counted in `holdings_count`.
- **Tax estimation** (plan § 4.15.5) — PSX 15% short-term / 12.5% long-term, US 22% (income proxy) short-term / 15% long-term, threshold 365 days. Only positive gains are taxed; losses are flagged as tax-loss-harvesting opportunities; short-term lots within 30 days of the long-term threshold are flagged for tax efficiency. Mixed-currency portfolios get a no-FX-conversion note.

### Portfolio Analyst Agent (6th agent, `backend/agents/portfolio_analyst_agent.py`)

Same two-path design as the Report Writer:

- **Deterministic path** (always on, fully offline): 0-100 health score penalised for single-position concentration (>25%), sector concentration (>40%), thin diversification (vs risk-profile target), and drawdowns; surfaces strengths, weaknesses, concrete recommendations ("Consider trimming ENGRO…"), tax-loss opportunities, concentration + delisting warnings.
- **LLM path** (preferred when `OPENROUTER_API_KEY` set): `llm_service.analyze_portfolio` synthesises the narrative; output validated/clamped, score bounded to 0-100, and the concrete tax-loss / concentration lists stay deterministic (grounded in real holdings).

### REST endpoints (`backend/api/portfolio.py`, registered in `main.py`)

All auth-gated + user-scoped:

- `GET /api/portfolio` — enriched holdings + metrics (`?refresh=true` bypasses price cache).
- `GET /api/portfolio/metrics` — aggregate metrics only.
- `GET /api/portfolio/performance?range=30d|90d|1y|all` — snapshot series for the chart (422 on bad range).
- `POST /api/portfolio/holdings` — add holding, **auto-logs a BUY transaction**; 404 unknown ticker, 422 bad quantity/price.
- `PATCH /api/portfolio/holdings/{id}` — partial update; `DELETE /api/portfolio/holdings/{id}` — 204 / 404.
- `POST /api/portfolio/transactions` — manual BUY/SELL; `GET /api/portfolio/transactions` — history with ticker/market filters; `GET /api/portfolio/transactions/export.csv` — CSV download.
- `GET /api/portfolio/tax-estimate` — per-lot + total estimated CGT if sold today.
- `POST /api/portfolio/analyze` — runs the Analyst Agent, persists a `PortfolioAnalysis` (400 when there are no active holdings); `GET /api/portfolio/analyses/latest` — most recent.

### Daily snapshot worker (`backend/workers/portfolio_snapshot.py`)

`run_portfolio_snapshots(db=None, *, snapshot_date=None)` — for each user with active holdings, computes value and **upserts** today's snapshot (per-user failure isolation). Callable directly from async; Celery-beat wiring is deployment glue. Verified idempotent (re-run same day updates, not duplicates).

### Tests

- **27 new offline tests** across `test_portfolio_service.py` (14: enrichment, metrics, build-portfolio price isolation, PSX/US tax short-vs-long-term, loss-harvest + near-threshold flags, analyst concentration/tax-loss/delisting + LLM-path) and `test_portfolio_api.py` (13: auth gating, holdings CRUD + auto-BUY, user-scoping, validation, live-P&L view, CSV export, tax endpoint, analyze persist + latest, performance range validation, snapshot-worker upsert powering the chart).
- **Full backend suite: 287 passed, 8 deselected (live).** No regressions in the prior 260 tests.

### Phase 7 — closed out (backend)

- ~~Holdings + transactions schema + CRUD.~~ ✅
- ~~Real-time P&L + portfolio-wide metrics.~~ ✅ (sector/market allocation, best/worst performer.)
- ~~Transaction history + CSV export.~~ ✅
- ~~Daily snapshot worker + performance series.~~ ✅
- ~~Portfolio Analyst Agent (6th agent) + analysis persistence.~~ ✅ (LLM + deterministic.)
- ~~Tax estimation (PSX CGT + US rules).~~ ✅

**Deferred (intentional):** frontend portfolio pages/components (Recharts), live-FX conversion for mixed-currency totals, FIFO cost-basis toggle, stock-split admin tool, dividend tracking (plan v2), Celery-beat scheduling of the snapshot worker.

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
