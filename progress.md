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

## Phase 2 — Price Agent MVP 🟡 (in progress)

In flight on `feat/price-agent`.

**Working**
- Backend `agents/price_agent.py` routes by market — `yfinance` for global, Playwright sync API for PSX (wrapped in `asyncio.to_thread`, with `WindowsProactorEventLoopPolicy` swap because psycopg-async uses the Selector loop elsewhere)
- Redis cache at `services/cache_service.py` with 60s TTL; tests use db 15 with autouse flush
- `GET /api/stocks/{ticker}/price` → `PriceQuote` JSON; 404 if unknown ticker, 502 if upstream fails
- Frontend stock detail page `/dashboard/stocks/[ticker]` with live `PriceCard` (TanStack Query, 30s refetch)
- Tests: 13 added (cache service, agent stubs, endpoint behaviour) — total backend suite now **40/40 green**

**Verified live**
- Global / yfinance: AAPL → price $300.23, +0.68%, full OHLC + 52w + market cap + P/E + EPS + dividend yield
- PSX / Playwright scrape: ENGRO → PKR 485.38, +1.48% with OHLC + volume

**Known gaps on PSX (tested across 8 tickers: ENGRO, HBL, OGDC, LUCK, NESTLE, SYS, FFC, MEBL)**

| Field | Coverage | Notes |
|---|---|---|
| price | 8/8 | always works |
| change / change_pct | 8/8 | always works |
| previous_close | 8/8 | derived from `price − change` |
| 52w high / low | 8/8 (filled) | **but NESTLE returned `[640, 105]`** — `data-low` / `data-high` attributes on dps.psx.com.pk look corrupt for tickers > PKR 1000 |
| open, day_high, day_low, volume | 4/8 | ~half of tickers return `0` after PSX market close — the page strips intra-day stats once the session ends |
| market_cap | 0/8 | not surfaced on the `/company/<X>` page at all |
| pe_ratio | 0/8 | field exists but PSX shows "N/A" for every ticker tested |
| eps | 0/8 | not on this page |
| dividend_yield | 0/8 | not on this page |

**Follow-up planned**
- Phase 4 (filings RAG) is the natural home for PSX fundamentals — annual reports already carry market cap, P/E, EPS, payout history
- Investigate the 52w-range bug for high-priced tickers (looks like a units issue in the page's data attributes)
- After-hours OHLC will need either: a second scrape against PSX's per-symbol historical endpoint, or accepting `null` post-close and relying on cache from the last open session

---

## Phase 3 — News + Sentiment Agents ⏳

Not started. Per plan § 4.5 / § 4.7: scraping Business Recorder / Dawn / Profit Pakistan for PSX, NewsAPI + Yahoo Finance feed for global; Reddit + StockTwits for sentiment.

---

## Phase 4 — Filings RAG Agent ⏳

Not started. Per plan § 4.6: SEC EDGAR + PSX annual reports → Pinecone embeddings → grounded Q&A. **Natural home for PSX fundamentals that Phase 2 couldn't surface.**

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
- [ ] PSX scraper resilience: log selector failures so we get early warning when PSX redesigns.
- [ ] PSX 52w high/low correctness for high-priced tickers (see Phase 2 gaps).
- [ ] PSX fundamentals (market cap / P/E / EPS / div yield) via Phase 4 RAG.
- [ ] Add an integration-test marker (`@pytest.mark.live`) and gate the live PSX/yfinance hits behind it so CI doesn't depend on external network.
