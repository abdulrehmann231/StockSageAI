# StockSage AI — Multi-Agent Stock Research Analyst

<!-- 
  ============================================================
  PROJECT PLAN DOCUMENT
  ============================================================
  This is the master planning document for StockSage AI.
  It serves as the single source of truth for:
    - Architecture decisions
    - Feature specifications
    - Database schemas
    - API endpoint definitions
    - Build phases and timeline
    - Cost estimates
  
  Keep this document updated as the project evolves.
  Last updated: June 2026
  ============================================================
-->

> **Project:** A multi-agent AI platform that automates stock research for both Pakistani (PSX) and Global (US) markets. Built as a portfolio-grade, production-ready application.
>
> **Author:** Abdul Rehman M. Nasir
> **Stack:** Next.js 14, FastAPI, LangChain, Pinecone, PostgreSQL, Redis, Celery
> **Target Timeline:** 6-7 weeks to v1 launch

---

## 1. Project Vision

### 1.1 The Problem
<!-- Core pain point that motivates the entire project -->
Retail investors — especially in Pakistan — lack accessible, AI-powered research tools. Information is scattered across SEC filings, PSX announcements, news sites, social media, and finance Telegram/WhatsApp groups. Most investors either:
- Rely on unverified tips
- Spend hours manually researching
- Use expensive Bloomberg/Reuters terminals (out of reach for most)

### 1.2 The Solution
<!-- The 5-pillar value proposition -->
A multi-agent AI system that automatically:
1. Fetches live price + market data
2. Reads and summarizes financial filings (RAG)
3. Aggregates news with sentiment analysis
4. Scans social sentiment from finance communities
5. Generates a clean, plain-English investment report with Buy/Hold/Sell verdict

### 1.3 Dual Market Strategy
<!-- Key differentiator: first-class PSX support -->
- **Pakistani Market (PSX):** First-class support — KSE-100, all major sectors, PKR pricing, SBP rate context
- **Global Market (US):** NYSE, NASDAQ stocks, USD pricing, Fed rate context
- Unified UI with a market toggle

### 1.4 Key Differentiator
**No existing AI tool seriously supports PSX.** This makes the app genuinely useful in an underserved market while still demonstrating skills relevant to global recruiters.

---

## 2. High-Level Architecture

<!--
  Architecture Overview:
  The system follows a layered architecture:
    1. Client Layer (Next.js) - User interface
    2. API Gateway (FastAPI) - Request routing, auth, rate limiting
    3. Orchestration Layer - LangGraph for multi-agent coordination
    4. Agent Layer - Specialized AI agents (price, news, RAG, sentiment, report)
    5. Data Layer - PostgreSQL, Redis, Pinecone, S3
    6. Worker Layer - Celery background tasks
    7. External Sources - PSX, Yahoo Finance, SEC, Reddit, etc.
  
  Data flows both synchronously (API requests) and asynchronously
  (Celery workers, WebSocket streams for agent progress).
-->

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT (Next.js 14)                      │
│  Dashboard • Search • Report View • Chat • Watchlist • Alerts    │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ REST + WebSockets
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                       API GATEWAY (FastAPI)                      │
│   Auth • Rate Limiting • Request Routing • WebSocket Server      │
└─────────────────────────────────────────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
    ┌──────────────────┐ ┌──────────────┐ ┌──────────────┐
    │   ORCHESTRATOR   │ │  CHAT ENGINE │ │  ALERT ENGINE │
    │  (LangGraph)     │ │  (LangChain) │ │  (Celery)    │
    └──────────────────┘ └──────────────┘ └──────────────┘
              │
    ┌─────────┼─────────┬─────────┬─────────┐
    ▼         ▼         ▼         ▼         ▼
┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐
│ Price  ││ News   ││ RAG    ││Sentiment││Report │
│ Agent  ││ Agent  ││ Agent  ││ Agent   ││Writer │
└────────┘└────────┘└────────┘└────────┘└────────┘
    │         │         │         │
    ▼         ▼         ▼         ▼
┌─────────────────────────────────────────────┐
│              DATA LAYER                      │
│  PostgreSQL │ Redis │ Pinecone │ S3/Storage  │
└─────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│        BACKGROUND WORKERS (Celery)           │
│  News Scraper • Price Updater • Filings     │
│  Sentiment Scan • Alerts • Daily Digest     │
└─────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│           EXTERNAL DATA SOURCES              │
│  PSX • Yahoo Finance • SEC EDGAR • Reddit   │
│  Twitter/X • Business Recorder • Dawn       │
└─────────────────────────────────────────────┘
```

---

## 3. Tech Stack

<!--
  Technology Selection Rationale:
  Each tool was chosen based on:
    1. Community support and ecosystem maturity
    2. Free tier availability for development
    3. Production scalability
    4. Team familiarity and learning curve
    5. Industry recognition (recruiters know these tools)
-->

### 3.1 Frontend
<!-- Next.js 14 App Router with server components for performance -->
- **Framework:** Next.js 14 (App Router)
- **Styling:** Tailwind CSS + shadcn/ui
- **State:** Zustand (lightweight, simpler than Redux)
- **Data Fetching:** TanStack Query (React Query)
- **Charts:** Recharts (for price charts and sentiment graphs)
- **Real-time:** Socket.io-client (for live price updates, agent progress)
- **Auth:** NextAuth.js with credentials + Google OAuth

### 3.2 Backend
<!-- FastAPI chosen for async support and Python AI ecosystem -->
- **Framework:** FastAPI (Python 3.11+)
- **Agent Framework:** LangGraph (built on LangChain) — best for multi-agent orchestration
- **LLM Provider:** OpenRouter (gives access to GPT-4o, Claude, Gemini, etc. with one API)
- **Embeddings:** OpenAI text-embedding-3-small (cheap and good)
- **Background Jobs:** Celery + Redis as broker
- **WebSockets:** FastAPI WebSocket + Socket.io
- **Auth:** JWT tokens, bcrypt for hashing

### 3.3 Databases
<!-- Multi-database strategy: relational for structured data, vector for RAG, cache for speed -->
- **PostgreSQL:** Users, watchlists, reports, alerts, cached prices (Supabase for managed hosting)
- **Pinecone:** Vector DB for SEC filings + PSX annual reports RAG
- **Redis:** Caching, task queue, real-time price storage, rate limiting
- **S3-compatible (Cloudflare R2 or Supabase Storage):** Storing scraped PDFs, generated reports

### 3.4 External APIs & Data Sources

**Global Market:**
<!-- Free APIs preferred to minimize costs during development -->
- Yahoo Finance (via `yfinance` Python lib) — free, no API key
- Alpha Vantage — free tier, 25 req/day
- SEC EDGAR API — free, official
- NewsAPI — free tier, 100 req/day
- Reddit API (PRAW) — free
- Twitter/X — paid or alternative scraping

**Pakistani Market:**
<!-- PSX requires custom scrapers - no official API available -->
- PSX official site — scraping required (Selenium/Playwright)
- Business Recorder, Dawn Business, Profit Pakistan — RSS feeds + scraping
- Mettis Global — scraping
- Public Telegram channels — Telethon library

### 3.5 Infrastructure
<!-- All services chosen for free tier availability -->
- **Frontend Hosting:** Vercel (free tier)
- **Backend Hosting:** Railway or Render (managed FastAPI hosting)
- **Database:** Supabase (managed PostgreSQL + Auth + Storage)
- **Workers:** Railway background workers
- **Vector DB:** Pinecone serverless (free tier)
- **Cache/Queue:** Upstash Redis (free tier)
- **Monitoring:** Sentry (free tier)

### 3.6 Why This Stack
- **All free or near-free tiers** to start — keeps cost under $20/month
- **You already know:** Next.js, FastAPI, Pinecone, LangChain, PostgreSQL
- **Industry standard** — recruiters recognize every tool here
- **Scalable** — can handle 10k users without rewrites

---

## 4. Feature-by-Feature Implementation

### 4.1 User Authentication & Onboarding

**What it does:** Sign up, log in, set preferences (market focus, notification channel).

**Implementation:**
- NextAuth.js on frontend with Google OAuth + email/password
- FastAPI verifies JWT on every request via dependency injection
- On signup, user picks default market (PSX / Global / Both) and risk profile (Conservative / Moderate / Aggressive)
- Profile stored in `users` table

**Database Schema:**
```sql
users (
  id UUID PRIMARY KEY,
  email TEXT UNIQUE,
  password_hash TEXT,
  full_name TEXT,
  default_market TEXT, -- 'PSX' | 'GLOBAL' | 'BOTH'
  risk_profile TEXT,
  created_at TIMESTAMP,
  email_notifications BOOLEAN DEFAULT TRUE
)
```

---

### 4.2 Smart Stock Search

**What it does:** User types ticker or company name, gets autocomplete with market badges.

**Implementation:**
1. **Pre-populated `stocks` table** with all PSX and major US-listed stocks
2. On app load, frontend fetches and caches the stocks list (only metadata, ~5MB)
3. Search is client-side fuzzy matching (using `fuse.js`) — instant, no backend hits
4. Each result shows: ticker, company name, market flag (🇵🇰/🇺🇸), sector

**Database Schema:**
```sql
stocks (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  market TEXT, -- 'PSX' | 'NYSE' | 'NASDAQ'
  sector TEXT,
  industry TEXT,
  market_cap NUMERIC,
  currency TEXT, -- 'PKR' | 'USD'
  is_active BOOLEAN
)
```

**Data Population:**
- Run a one-time script to scrape PSX listed companies + fetch S&P 500 from a static dataset
- Refresh quarterly via Celery cron job

---

### 4.3 The Multi-Agent Orchestration System (Core Feature)

**What it does:** When user requests a report, orchestrator spawns 4 specialized agents in parallel, then a 5th agent synthesizes the output.

**Implementation with LangGraph:**

```python
# Pseudo-code structure
from langgraph.graph import StateGraph

class ResearchState(TypedDict):
    ticker: str
    market: str  # 'PSX' or 'GLOBAL'
    price_data: dict
    news_data: dict
    filings_data: dict
    sentiment_data: dict
    final_report: dict
    progress: list  # for streaming to frontend

def build_research_graph():
    graph = StateGraph(ResearchState)

    # Add nodes
    graph.add_node("price_agent", price_agent)
    graph.add_node("news_agent", news_agent)
    graph.add_node("filings_agent", filings_rag_agent)
    graph.add_node("sentiment_agent", sentiment_agent)
    graph.add_node("report_writer", report_writer_agent)

    # Parallel execution of 4 agents
    graph.set_entry_point("price_agent")
    # ... fan out and fan in logic
    graph.add_edge(["price_agent", "news_agent", "filings_agent", "sentiment_agent"], "report_writer")
    graph.set_finish_point("report_writer")

    return graph.compile()
```

**Flow:**
1. User hits `/api/reports/generate` with `{ticker: "ENGRO"}`
2. Backend determines market from `stocks` table
3. Creates a WebSocket connection back to frontend
4. Spawns orchestrator
5. Each agent streams progress: *"News Agent: Scraping Business Recorder..."*
6. Frontend shows agent progress in real-time (cool UX)
7. Once all done, Report Writer agent synthesizes
8. Final report saved to DB + streamed to user

**Why LangGraph over plain LangChain:**
- Built for multi-agent workflows
- Native parallel execution
- State management is explicit
- Easy to add new agents later (e.g., Technical Analysis Agent v2)

---

### 4.4 Agent 1 — Price/Market Data Agent

**Job:** Fetch live market data fastest, so user sees *something* immediately.

**Implementation:**
- **For Global stocks:** Use `yfinance` library
- **For PSX stocks:** Scraper using `playwright` (handles JS rendering on dps.psx.com.pk)
- Cache results in Redis with 60-second TTL during market hours
- Returns: current price, open, high, low, volume, 52w high/low, P/E, EPS, dividend yield

**File:** `backend/agents/price_agent.py`

**Key Detail:** During PSX market hours (9:30 AM - 3:30 PM PKT), prefer fresh scrape. After hours, use last close.

---

### 4.5 Agent 2 — News Agent

**Job:** Find recent relevant news, summarize, classify impact.

**Implementation:**
1. **For Global:** Hit NewsAPI + Yahoo Finance news feed for ticker
2. **For PSX:** Scrape Business Recorder, Profit Pakistan, Dawn Business using `httpx + BeautifulSoup`
3. Filter by relevance: must mention ticker or company name in title/first paragraph
4. Send articles to LLM with prompt:
   ```
   Summarize each article in 2 sentences.
   Classify impact: HIGH_POSITIVE, MEDIUM_POSITIVE, NEUTRAL, MEDIUM_NEGATIVE, HIGH_NEGATIVE.
   Extract catalysts: earnings, dividend, M&A, regulatory, executive change, product, lawsuit.
   ```
5. Return top 5 stories sorted by impact + recency

**File:** `backend/agents/news_agent.py`

**Caching:** Cache scraped articles for 30 minutes in Redis to avoid re-scraping on chat follow-ups.

---

### 4.6 Agent 3 — Filings RAG Agent (Most Technically Impressive)

**Job:** Answer questions from actual SEC filings or PSX annual reports.

**Implementation — One-time setup:**

1. **For Global (SEC):**
   - Use SEC EDGAR API to download 10-K and 10-Q PDFs for top 500 US stocks
   - Parse using `pypdf` or `pdfplumber`
   - Chunk text (1000 tokens, 200 overlap)
   - Embed using OpenAI `text-embedding-3-small`
   - Upsert to Pinecone with metadata: `{ticker, filing_type, year, section}`

2. **For PSX:**
   - Scrape annual reports from PSX company pages
   - This is the hard part — reports are PDFs, often poorly formatted
   - Use `pypdf` + LLM cleanup for messy ones
   - Same chunking + embedding pipeline
   - Pinecone namespace: `psx_filings`

**Runtime flow:**
1. Agent receives ticker
2. Auto-generates 5 key questions: revenue trend, profit margin, debt level, risks, outlook
3. For each question:
   - Embed the question
   - Search Pinecone filtered by `ticker`
   - Top 5 chunks → feed to LLM with question
   - Get grounded answer with citations (page #, filing year)
4. Compile into structured `filings_data` object

**File:** `backend/agents/filings_agent.py`

**Refresh:** Celery weekly job re-scrapes new filings and indexes them.

---

### 4.7 Agent 4 — Sentiment Agent

**Job:** Gauge public/community sentiment.

**Implementation:**

**For Global:**
- Reddit via PRAW: search r/stocks, r/investing, r/wallstreetbets for ticker, last 30 days
- StockTwits API (free) for ticker stream
- Get last 50-100 posts/comments
- Send batch to LLM:
  ```
  Score sentiment: -1 (very bearish) to +1 (very bullish).
  Identify top 3 bullish reasons and top 3 bearish concerns.
  ```

**For PSX:**
- Scrape public PSX Telegram channels using Telethon
- Scrape Twitter/X with `snscrape` or paid X API for Pakistani finance accounts
- Same LLM analysis

**Return:**
```json
{
  "overall_sentiment": 0.34,
  "bullish_pct": 67,
  "bearish_pct": 33,
  "top_bullish_points": ["Strong Q3 earnings expected", ...],
  "top_bearish_points": ["High debt levels", ...],
  "post_count": 142
}
```

**File:** `backend/agents/sentiment_agent.py`

---

### 4.8 Agent 5 — Report Writer Agent

**Job:** Synthesize all 4 agents' outputs into a clean investment brief.

**Implementation:**
- Receives all 4 agent outputs in state
- Single LLM call with detailed prompt:
  ```
  You are a senior equity research analyst. Given the following data, write a structured
  investment report with these sections:
  1. Snapshot (1 paragraph)
  2. Recent Developments (from news)
  3. Financial Health (from filings)
  4. Market Sentiment (from social)
  5. Risk Factors
  6. Final Verdict: BUY / HOLD / SELL
  7. Confidence Score (0-100%)
  8. Reasoning (3-5 bullet points)

  Be honest. If data is mixed or weak, say HOLD. Don't be falsely bullish.
  ```
- Output in structured JSON (use OpenAI function calling for reliability)
- Store in `reports` table
- Stream to frontend section-by-section for nice UX

**File:** `backend/agents/report_writer_agent.py`

**Database Schema:**
```sql
reports (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users,
  ticker TEXT,
  market TEXT,
  report_data JSONB,
  verdict TEXT, -- 'BUY' | 'HOLD' | 'SELL'
  confidence INT,
  created_at TIMESTAMP
)
```

---

### 4.9 Chat With The Stock (Follow-up Q&A)

**What it does:** After report loads, user asks follow-up questions in chat.

**Implementation:**
- Once a report is generated, all underlying data (news, filings chunks, sentiment) is stored in Redis with 1-hour TTL keyed by report ID
- User's chat message → backend retrieves cached context → builds prompt with all context → LLM response
- For technical questions ("What's the P/E?"), no LLM needed — direct data lookup
- Chat history stored in `chat_messages` table

**Database Schema:**
```sql
chat_messages (
  id UUID PRIMARY KEY,
  report_id UUID REFERENCES reports,
  role TEXT, -- 'user' | 'assistant'
  content TEXT,
  created_at TIMESTAMP
)
```

**File:** `backend/api/chat.py`

---

### 4.10 Watchlist + Daily Briefings

**Watchlist:**
- User adds stocks to watchlist from any report or search
- Frontend: a separate `/watchlist` page showing all tracked stocks with mini-cards (price, change, mini sentiment)

**Daily Briefing:**
- Celery cron job runs at:
  - 9:15 AM PKT for Pakistani users (before PSX opens at 9:30)
  - 8:30 AM EST for US users (before NYSE opens at 9:30)
- For each user:
  - For each stock in watchlist, fetch latest price + check for big moves (>3%)
  - If big news in last 24h, include summary
  - Compile into email + in-app notification
  - Use Resend or SendGrid for email

**Database Schema:**
```sql
watchlist (
  user_id UUID,
  ticker TEXT,
  added_at TIMESTAMP,
  PRIMARY KEY (user_id, ticker)
)

briefings (
  id UUID PRIMARY KEY,
  user_id UUID,
  content JSONB,
  sent_at TIMESTAMP
)
```

**Files:**
- `backend/workers/daily_briefing.py`
- `frontend/app/watchlist/page.tsx`

---

### 4.11 Smart Alerts

**What it does:** User sets rules → backend monitors → notifications fire.

**Alert Types:**
- Price drops X% in a day
- Price reaches X target
- New filing released
- Sentiment shifts (e.g., goes from bullish to bearish)
- Big news (HIGH_POSITIVE or HIGH_NEGATIVE)

**Implementation:**
1. User creates alert via UI
2. Celery beat job every 15 mins:
   - Loops through all active alerts
   - Checks condition against latest data
   - If triggered: send email/push notif + mark as fired
3. Alerts have cooldown periods (don't spam)

**Database Schema:**
```sql
alerts (
  id UUID PRIMARY KEY,
  user_id UUID,
  ticker TEXT,
  alert_type TEXT, -- 'PRICE_DROP', 'PRICE_TARGET', 'NEW_FILING', etc.
  condition JSONB, -- e.g., {"threshold": -5, "period": "1d"}
  is_active BOOLEAN,
  last_triggered TIMESTAMP,
  cooldown_hours INT DEFAULT 24,
  created_at TIMESTAMP
)
```

**File:** `backend/workers/alert_engine.py`

---

### 4.12 Compare Mode

**What it does:** Side-by-side analysis of 2-4 stocks.

**Implementation:**
- New route `/compare?tickers=ENGRO,FFC,FATIMA`
- Triggers parallel report generation for all stocks (reuses single-stock orchestrator)
- Once all done, Report Writer is called in *comparison mode* with a different prompt
- Output: a comparison table + a paragraph on which is most attractive and why

**File:** `frontend/app/compare/page.tsx`, `backend/api/compare.py`

---

### 4.13 Macro Context Panel

**What it does:** Always-visible bar showing market-wide context.

**Implementation:**
- Celery job updates every 30 mins:
  - PSX: KSE-100 level + change, USD/PKR, SBP policy rate
  - Global: S&P 500, NASDAQ, Fed rate, 10Y treasury yield
- Stored in Redis with key `macro:psx` and `macro:global`
- Frontend fetches on every page load (cached)
- Report Writer agent uses this as additional context

**Why this matters:** A "BUY" signal at SBP policy rate 22% means something very different from one at 12%.

**File:** `backend/workers/macro_updater.py`

---

### 4.14 Learning Mode (Beginner-Friendly)

**What it does:** Pakistani retail investors are often new — add educational layer.

**Implementation:**
- Every financial term in reports has hover tooltip
- Terms stored in static JSON file with simple definitions
- "Explain Like I'm 5" toggle at report top — when on, regenerates report sections in simpler language
- Could be a v2 feature, but the tooltip system is easy and adds polish

**File:** `frontend/lib/glossary.json`, `frontend/components/Tooltip.tsx`

---

### 4.15 Portfolio Tracker

**What it does:** Lets users add their actual stock holdings, tracks real-time P&L, and uses the existing multi-agent system to give AI-powered rebalancing advice on their actual portfolio.

**Why it matters:**
- Watchlist = stocks user is *watching*
- Portfolio = stocks user *actually owns*
- This is the feature that transforms the app from "research tool" to "investment companion"
- Users return daily to check P&L → high engagement → strong portfolio piece

**Core Capabilities:**

1. **Manual Holdings Entry**
   - User adds: ticker, quantity, avg buy price, buy date (optional), notes
   - Supports multiple lots of same stock (bought ENGRO at 280, then 320 — both tracked)
   - Edit/delete holdings
   - Mark as sold (moves to "Sold Holdings" history for tax purposes)

2. **Real-Time P&L Calculation**
   - For each holding: `current_value = quantity × current_price`
   - `gain_loss = current_value - (quantity × avg_buy_price)`
   - `gain_loss_pct = (gain_loss / cost_basis) × 100`
   - Updates live via the same WebSocket price stream used by watchlist
   - Color-coded: green for gains, red for losses

3. **Portfolio-Wide Metrics Dashboard**
   - Total portfolio value (in PKR + USD if mixed markets)
   - Total invested (cost basis)
   - Total gain/loss (absolute + %)
   - Day's change (how much portfolio moved today)
   - Best performer (highest %)
   - Worst performer (lowest %)
   - Sector allocation pie chart (Recharts)
   - Market split: % in PSX vs % in Global

4. **AI-Powered Rebalancing Suggestions**
   - User clicks **"Analyze My Portfolio"**
   - Backend runs the multi-agent orchestrator on each holding in parallel
   - A new **Portfolio Analyst Agent** synthesizes all individual reports + portfolio metrics
   - Outputs:
     - Overall portfolio health score (0-100)
     - Diversification analysis (over-concentrated in any sector?)
     - Risk assessment (too aggressive/conservative for user's risk profile?)
     - Specific suggestions: *"Consider trimming ENGRO — 35% of portfolio is too concentrated"*
     - Tax-aware advice: *"FFC has 8% loss — consider selling for tax-loss harvesting"*

5. **Tax Implication Notes**
   - **Pakistan:** Capital Gains Tax rules (currently 15% on stocks held <12 months, 12.5% if >12 months — auto-update if SBP/FBR changes)
   - **US:** Short-term (held <1 year, taxed as income) vs Long-term (>1 year, 15-20%)
   - Shows estimated tax liability if sold today
   - Highlights holdings near the 12-month threshold (tax efficiency opportunity)

6. **Transaction History**
   - Every buy/sell logged with timestamp
   - Filterable by date range, ticker, market
   - Exportable as CSV (for accountant or tax filing)

7. **Performance Over Time**
   - Snapshot of portfolio value taken daily via Celery job
   - Line chart showing portfolio growth over 30d / 90d / 1y / All
   - Compare against benchmark: KSE-100 (for PSX-heavy users) or S&P 500 (for Global users)

8. **Cost Basis Methods**
   - Default: Average cost
   - Advanced toggle: FIFO (First-In-First-Out) — important for accurate tax calculation
   - Different countries have different rules, so make this configurable

**Implementation Details:**

**Database Schema:**
```sql
-- Holdings (current positions)
CREATE TABLE holdings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    ticker TEXT REFERENCES stocks(ticker),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    avg_buy_price NUMERIC NOT NULL CHECK (avg_buy_price > 0),
    buy_date DATE,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Transaction history (all buys and sells)
CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    holding_id UUID REFERENCES holdings(id),
    ticker TEXT REFERENCES stocks(ticker),
    transaction_type TEXT CHECK (transaction_type IN ('BUY', 'SELL')),
    quantity NUMERIC NOT NULL,
    price NUMERIC NOT NULL,
    transaction_date DATE NOT NULL,
    fees NUMERIC DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Daily portfolio snapshots (for performance charts)
CREATE TABLE portfolio_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    total_value NUMERIC NOT NULL,
    total_cost_basis NUMERIC NOT NULL,
    total_gain_loss NUMERIC NOT NULL,
    snapshot_date DATE NOT NULL,
    breakdown JSONB, -- per-ticker breakdown
    UNIQUE(user_id, snapshot_date)
);

-- Portfolio analysis reports (from AI agent)
CREATE TABLE portfolio_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    health_score INT CHECK (health_score BETWEEN 0 AND 100),
    analysis_data JSONB,
    recommendations JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_holdings_user ON holdings(user_id) WHERE is_active = TRUE;
CREATE INDEX idx_transactions_user ON transactions(user_id, transaction_date DESC);
CREATE INDEX idx_snapshots_user_date ON portfolio_snapshots(user_id, snapshot_date DESC);
```

**API Endpoints:**
```
GET    /api/portfolio                    # Full portfolio with live P&L
GET    /api/portfolio/metrics            # Aggregate metrics
GET    /api/portfolio/performance?range=30d   # Historical chart data

POST   /api/portfolio/holdings           # Add new holding (auto-creates BUY transaction)
PATCH  /api/portfolio/holdings/:id       # Update holding
DELETE /api/portfolio/holdings/:id       # Remove holding

POST   /api/portfolio/transactions       # Manual transaction (buy more, sell some)
GET    /api/portfolio/transactions       # History with filters
GET    /api/portfolio/transactions/export.csv

POST   /api/portfolio/analyze            # Trigger AI rebalancing analysis
GET    /api/portfolio/analyses/latest    # Get most recent analysis

GET    /api/portfolio/tax-estimate       # Estimated tax liability if sold today
```

**Portfolio Analyst Agent (New 6th Agent):**

File: `backend/agents/portfolio_analyst_agent.py`

```python
# Pseudo-code structure
async def portfolio_analyst_agent(state: PortfolioState):
    """
    Given user's holdings + individual stock reports + user's risk profile,
    produce holistic portfolio advice.
    """
    holdings = state["holdings"]
    individual_reports = state["individual_reports"]  # from running orchestrator per holding
    user_profile = state["user_profile"]
    macro_context = state["macro_context"]

    prompt = f"""
    You are a senior portfolio manager. Analyze this portfolio:

    User risk profile: {user_profile["risk_profile"]}
    Total value: {holdings_total}
    Holdings: {holdings_summary}

    Individual stock analyses: {individual_reports}
    Current macro context: {macro_context}

    Provide:
    1. Health score 0-100 (consider diversification, risk match, performance)
    2. Top 3 strengths of this portfolio
    3. Top 3 weaknesses or risks
    4. Specific actionable recommendations (max 5)
    5. Tax-loss harvesting opportunities
    6. Concentration warnings (any single stock > 25% of portfolio?)

    Output as structured JSON.
    """

    return await llm.call(prompt, response_format="json")
```

**Frontend Pages:**
- `frontend/app/portfolio/page.tsx` — Main portfolio dashboard
- `frontend/app/portfolio/holdings/page.tsx` — Detailed holdings table
- `frontend/app/portfolio/transactions/page.tsx` — Transaction history
- `frontend/app/portfolio/analysis/page.tsx` — AI analysis view

**Frontend Components:**
- `PortfolioSummaryCard.tsx` — Top of dashboard, big numbers
- `HoldingsTable.tsx` — Sortable table of all positions
- `AddHoldingModal.tsx` — Form to add new position
- `PortfolioChart.tsx` — Performance over time (Recharts line chart)
- `SectorAllocationChart.tsx` — Pie chart of sector exposure
- `TaxEstimateCard.tsx` — Shows potential tax liability
- `RebalancingSuggestionsPanel.tsx` — AI recommendations display

**New Background Worker:**
- `portfolio_snapshot_daily` — Runs daily at 5 PM PKT (after PSX close) and 5 PM EST (after NYSE close)
  - For each user with holdings, computes total value and writes a `portfolio_snapshots` row
  - Powers the performance chart

**Caching Strategy:**
- Portfolio metrics cached in Redis with 60-second TTL during market hours
- Invalidated immediately when user adds/removes/updates a holding
- AI analysis results cached for 24 hours (expensive to regenerate)

**Edge Cases to Handle:**
- Stock delisted from PSX → mark holding as "Inactive — stock delisted"
- Currency mismatch (Pakistani user holds US stocks) → show both PKR and USD totals using live FX rate from macro updater
- Stock split → admin tool to apply split ratio across all affected holdings
- Dividend tracking (v2) — add `dividends` table for received payouts

**Where it fits in build plan:**
- Add as **Phase 7** (between current Phase 6 alerts and Phase 7 polish)
- Estimated: 4-5 days of work
- Build order within phase:
  1. Database schema + basic CRUD (1 day)
  2. Portfolio dashboard UI + live P&L (1 day)
  3. Transaction history + CSV export (0.5 day)
  4. Daily snapshot worker + performance chart (1 day)
  5. Portfolio Analyst Agent + analysis UI (1.5 days)
  6. Tax estimation (0.5 day)

**Why this strengthens your CV:**
- Shows you can build **financial calculation logic** (P&L, cost basis, tax) — not just AI wrappers
- Demonstrates **stateful, data-heavy features** (snapshots, historical tracking)
- Adds another **AI agent** to the orchestration story (now 6 agents working together)
- Real product value — users come back daily, this drives retention

---

## 5. Background Workers (Celery)

All scheduled tasks running 24/7.

| Worker | Schedule | What It Does |
|---|---|---|
| `price_updater` | Every 1 min (market hours) | Updates Redis cache for watchlist tickers |
| `news_scraper` | Every 30 min | Scrapes news sites, stores in Redis |
| `sentiment_scanner` | Every 2 hours | Recomputes sentiment for top tracked stocks |
| `filings_checker` | Daily 6 AM UTC | Checks SEC EDGAR + PSX for new filings, indexes to Pinecone |
| `alert_engine` | Every 15 min | Evaluates all active alerts |
| `daily_briefing_psx` | 9:15 AM PKT | Sends emails to PSX users |
| `daily_briefing_global` | 8:30 AM EST | Sends emails to Global users |
| `macro_updater` | Every 30 min | Updates KSE-100, S&P 500, rates |
| `portfolio_snapshot_psx` | 5 PM PKT daily | Snapshots PSX-holdings users' portfolio values |
| `portfolio_snapshot_global` | 5 PM EST daily | Snapshots Global-holdings users' portfolio values |
| `vector_db_maintenance` | Weekly Sunday 3 AM | Cleans up old Pinecone entries |

**Setup:**
- `celery beat` for scheduling
- `celery worker` for execution (run multiple processes for parallelism)
- Monitor via Flower dashboard

---

## 6. Database Schema (Complete)

```sql
-- Users
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    full_name TEXT,
    default_market TEXT CHECK (default_market IN ('PSX', 'GLOBAL', 'BOTH')),
    risk_profile TEXT,
    email_notifications BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Stocks (master list)
CREATE TABLE stocks (
    ticker TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    sector TEXT,
    industry TEXT,
    market_cap NUMERIC,
    currency TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Reports
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    ticker TEXT REFERENCES stocks(ticker),
    market TEXT,
    report_data JSONB,
    verdict TEXT CHECK (verdict IN ('BUY', 'HOLD', 'SELL')),
    confidence INT CHECK (confidence BETWEEN 0 AND 100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Chat messages
CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID REFERENCES reports(id),
    role TEXT CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Watchlist
CREATE TABLE watchlist (
    user_id UUID REFERENCES users(id),
    ticker TEXT REFERENCES stocks(ticker),
    added_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (user_id, ticker)
);

-- Alerts
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    ticker TEXT REFERENCES stocks(ticker),
    alert_type TEXT NOT NULL,
    condition JSONB NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    last_triggered TIMESTAMP,
    cooldown_hours INT DEFAULT 24,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Briefings sent
CREATE TABLE briefings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    content JSONB,
    sent_at TIMESTAMP DEFAULT NOW()
);

-- News cache (for analytics + audit)
CREATE TABLE news_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT,
    source TEXT,
    title TEXT,
    url TEXT,
    summary TEXT,
    sentiment TEXT,
    published_at TIMESTAMP,
    scraped_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_reports_user ON reports(user_id, created_at DESC);
CREATE INDEX idx_reports_ticker ON reports(ticker, created_at DESC);
CREATE INDEX idx_alerts_active ON alerts(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_news_ticker ON news_cache(ticker, published_at DESC);
```

---

## 7. API Endpoints

```
POST   /api/auth/signup
POST   /api/auth/login
GET    /api/auth/me

GET    /api/stocks/search?q=engr
GET    /api/stocks/:ticker

POST   /api/reports/generate         # body: { ticker }
GET    /api/reports/:id
GET    /api/reports/user             # list user's reports
WS     /api/reports/stream/:id       # WebSocket for agent progress

POST   /api/chat/:reportId/message   # body: { content }
GET    /api/chat/:reportId/history

GET    /api/watchlist
POST   /api/watchlist                # body: { ticker }
DELETE /api/watchlist/:ticker

GET    /api/alerts
POST   /api/alerts
DELETE /api/alerts/:id
PATCH  /api/alerts/:id

POST   /api/compare                  # body: { tickers: [...] }

GET    /api/macro/psx
GET    /api/macro/global
```

---

## 8. Frontend Page Structure

```
app/
├── (auth)/
│   ├── login/page.tsx
│   └── signup/page.tsx
├── (dashboard)/
│   ├── page.tsx                    # Home: search + trending + recent reports
│   ├── watchlist/page.tsx
│   ├── reports/
│   │   ├── page.tsx                # All user reports
│   │   └── [id]/page.tsx           # Single report + chat
│   ├── compare/page.tsx
│   ├── alerts/page.tsx
│   └── settings/page.tsx
├── layout.tsx
└── globals.css

components/
├── ui/                              # shadcn components
├── agents/
│   ├── AgentProgress.tsx           # Live agent status during report gen
│   └── AgentCard.tsx
├── reports/
│   ├── ReportView.tsx
│   ├── VerdictBadge.tsx
│   ├── SentimentChart.tsx
│   └── NewsTimeline.tsx
├── chat/
│   ├── ChatPanel.tsx
│   └── MessageBubble.tsx
└── macro/
    └── MacroBar.tsx                 # Top bar with KSE-100, etc.
```

---

## 9. Repository Structure (Monorepo)

```
stocksage-ai/
├── frontend/                        # Next.js 14
│   ├── app/
│   ├── components/
│   ├── lib/
│   ├── package.json
│   └── ...
├── backend/                         # FastAPI
│   ├── api/                         # Route handlers
│   │   ├── auth.py
│   │   ├── reports.py
│   │   ├── chat.py
│   │   ├── watchlist.py
│   │   ├── alerts.py
│   │   ├── portfolio.py
│   │   └── compare.py
│   ├── agents/
│   │   ├── orchestrator.py          # LangGraph setup
│   │   ├── price_agent.py
│   │   ├── news_agent.py
│   │   ├── filings_agent.py
│   │   ├── sentiment_agent.py
│   │   ├── report_writer_agent.py
│   │   └── portfolio_analyst_agent.py
│   ├── workers/                     # Celery tasks
│   │   ├── celery_app.py
│   │   ├── price_updater.py
│   │   ├── news_scraper.py
│   │   ├── alert_engine.py
│   │   ├── daily_briefing.py
│   │   ├── filings_checker.py
│   │   ├── portfolio_snapshot.py
│   │   └── macro_updater.py
│   ├── scrapers/                    # PSX-specific scrapers
│   │   ├── psx_prices.py
│   │   ├── psx_filings.py
│   │   ├── business_recorder.py
│   │   ├── dawn_business.py
│   │   └── profit_pakistan.py
│   ├── services/
│   │   ├── llm_service.py           # OpenRouter wrapper
│   │   ├── embeddings_service.py
│   │   ├── pinecone_service.py
│   │   └── cache_service.py
│   ├── db/
│   │   ├── models.py                # SQLAlchemy models
│   │   ├── schemas.py               # Pydantic schemas
│   │   └── session.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   └── deps.py
│   ├── main.py
│   └── requirements.txt
├── scripts/                         # One-off scripts
│   ├── populate_stocks.py
│   ├── seed_filings.py
│   └── backfill_sentiment.py
├── docker-compose.yml               # Local dev: Postgres + Redis
├── plan.md                          # THIS FILE
└── README.md
```

---

## 10. Phased Build Plan (6-7 Weeks)

<!--
  Build Strategy:
  - Each phase has a clear deliverable that can be demoed
  - Phases are ordered by dependency (later phases build on earlier ones)
  - MVP is achieved at end of Phase 5 (full report generation)
  - Phases 6-9 add polish and advanced features
  - Each phase should result in a deployable increment
-->

### Phase 0 — Setup (2-3 days)
<!-- Foundation: accounts, configs, local dev environment -->
- Initialize monorepo
- Set up Supabase, Pinecone, Upstash Redis accounts
- Configure OpenRouter API key
- Local Docker Compose for Postgres + Redis
- Deploy hello-world to Vercel + Railway

### Phase 1 — Auth + Stock Search (Week 1)
<!-- Core user flow: signup -> search -> view stock -->
- Implement signup/login/JWT
- Build `stocks` table population script
- Stock search with autocomplete
- Basic dashboard layout
- **Deliverable:** User can sign up and search any stock

### Phase 2 — Single Agent (Price Agent) MVP (Week 1-2)
<!-- First agent: proves the multi-agent architecture works -->
- Build Price Agent for both PSX and Global
- Stock detail page showing live price + basic info
- WebSocket for live price updates on watchlist
- **Deliverable:** User can see live prices for any stock

### Phase 3 — News + Sentiment Agents (Week 2-3)
<!-- Data agents: gathering and analyzing external information -->
- Build News Agent with Pakistani + Global scrapers
- Build Sentiment Agent (Reddit + scraping)
- Add to report flow
- **Deliverable:** User gets news summaries and sentiment scores

### Phase 4 — Filings RAG Agent (Week 3-4)
<!-- Most technically complex agent: vector search over financial documents -->
- One-time SEC EDGAR scraping for top 100 US stocks
- PSX annual report scraping for KSE-100 stocks
- Pinecone indexing pipeline
- RAG agent runtime
- **Deliverable:** User gets answers from actual filings

### Phase 5 — Orchestration + Report Writer (Week 4)
<!-- Integration phase: connect all agents, generate final reports -->
- LangGraph multi-agent setup
- Report Writer agent
- WebSocket streaming of progress
- Final report UI
- **Deliverable:** Full investment reports generated

### Phase 6 — Chat + Watchlist + Alerts (Week 5)
<!-- Personalization: user-specific features -->
- Chat with stock feature
- Watchlist CRUD
- Alert engine + Celery scheduling
- **Deliverable:** Full personalization features

### Phase 7 — Portfolio Tracker (Week 5-6, 4-5 days)
<!-- Advanced feature: transforms app from research tool to investment companion -->
- Holdings + transactions database schema
- Manual holdings entry UI
- Real-time P&L calculation + portfolio dashboard
- Daily portfolio snapshot Celery worker
- Performance over time chart
- Portfolio Analyst Agent (6th agent)
- Tax estimation logic (PSX CGT + US capital gains rules)
- Transaction history + CSV export
- **Deliverable:** Users can track actual holdings with AI-powered rebalancing advice

### Phase 8 — Polish + Macro + Daily Briefings (Week 6)
<!-- Final polish: UX improvements and production readiness -->
- Macro context panel
- Daily briefing emails
- Compare mode
- Learning mode tooltips
- UI polish (animations, loading states)
- **Deliverable:** Production-ready v1

### Phase 9 — Launch
<!-- Go live: deploy, document, share -->
- Deploy production
- Write portfolio case study
- Record demo video
- Share on LinkedIn, Twitter, Reddit r/PakistaniInvestors, r/stocks
- Add to GitHub with great README

---

## 11. Environment Variables

```bash
# Frontend (.env.local)
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXTAUTH_URL=http://localhost:3000
NEXTAUTH_SECRET=<random>
GOOGLE_CLIENT_ID=<from Google Cloud>
GOOGLE_CLIENT_SECRET=<from Google Cloud>

# Backend (.env)
DATABASE_URL=postgresql://...        # Supabase
REDIS_URL=redis://...                 # Upstash
PINECONE_API_KEY=<key>
PINECONE_INDEX_NAME=stocksage
PINECONE_FILINGS_NAMESPACE_GLOBAL=sec_filings
PINECONE_FILINGS_NAMESPACE_PSX=psx_filings

OPENROUTER_API_KEY=<key>            # Primary - unified gateway for all models
OPENAI_API_KEY=<for embeddings>     # Used only for text-embedding-3-small
GEMINI_API_KEY=<key>                # Optional - direct Gemini for free tier maximization
GROQ_API_KEY=<key>                  # Optional - direct Groq for sentiment agent

NEWSAPI_KEY=<key>
ALPHA_VANTAGE_KEY=<key>

REDDIT_CLIENT_ID=<key>
REDDIT_CLIENT_SECRET=<secret>

RESEND_API_KEY=<for emails>

JWT_SECRET=<random>
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080            # 7 days

SENTRY_DSN=<for monitoring>
```

---

## 12. AI Model Strategy (Per Agent)

This section defines exactly which LLM to use for each agent, balancing cost, quality, and free tier usage.

### 12.1 Core Philosophy

**Don't use one model for everything.** Each agent has different requirements:
- Some need speed (price data routing)
- Some need volume (sentiment analysis on 100s of posts)
- Some need reasoning quality (report writing, portfolio analysis)
- Some need long context (filings RAG)

A smart routing strategy can give 80% of GPT-4o quality at 20% of the cost.

### 12.2 Model Routing Per Agent

| Agent | Model | Provider | Why | Approx Cost/Call |
|---|---|---|---|---|
| News Agent | Gemini 2.0 Flash | OpenRouter / Google | Fast summarization, large context window, cheap | ~$0.001 |
| Sentiment Agent | Llama 3.3 70B | Groq | High volume, simple classification, free tier is generous | Free / $0.0005 |
| Filings RAG Agent | Claude 3.5 Sonnet | Anthropic / OpenRouter | Best at reasoning over long financial documents | ~$0.01 |
| Report Writer Agent | Claude 3.5 Sonnet ⭐ | Anthropic / OpenRouter | Best at structured, nuanced financial writing | ~$0.02 |
| Portfolio Analyst Agent | Claude 3.5 Sonnet | Anthropic / OpenRouter | Complex multi-factor reasoning required | ~$0.02 |
| Chat (follow-ups) | Gemini 2.0 Flash | OpenRouter / Google | Cheap, fast, context already loaded from report | ~$0.001 |
| Embeddings | text-embedding-3-small | OpenAI | Industry standard, very cheap, good quality | $0.02 per 1M tokens |

**Total cost per full report generation:** ~$0.05-0.10
**At 100 reports/month for testing:** ~$5-10

### 12.3 Free Tier Strategy (During Development)

While building and testing, use these free tiers to get to **$0/month**:

**Google Gemini API (Free Tier)** ⭐ Best free option
- Gemini 2.0 Flash: 15 requests/min, 1500/day
- Gemini 2.5 Pro: Lower limits but very capable
- Generous quotas for development
- Get key at: aistudio.google.com

**Groq API (Free Tier)**
- Llama 3.3 70B, Mixtral, Gemma 2
- ~30 req/min free
- Insanely fast (10x faster than OpenAI)
- Perfect for sentiment agent during dev
- Get key at: console.groq.com

**OpenRouter Free Models**
- Models tagged `:free` (e.g. `deepseek/deepseek-chat-v3:free`)
- Quality is decent for non-critical paths
- Sometimes throttled, not for production

**OpenAI Free Credit**
- New accounts get $5 credit
- Use for embeddings during initial Pinecone seeding
- After credit, switch to text-embedding-3-small (still cheap)

### 12.4 Production Configuration

Once ready for portfolio demo, use this exact setup in `backend/services/llm_service.py`:

```python
# Model routing configuration
MODELS = {
    "news_agent": "google/gemini-2.0-flash-exp:free",      # OpenRouter free
    "sentiment_agent": "groq/llama-3.3-70b-versatile",     # Groq free tier
    "filings_agent": "anthropic/claude-3.5-sonnet",        # Paid - quality matters
    "report_writer": "anthropic/claude-3.5-sonnet",        # Paid - user-facing
    "portfolio_analyst": "anthropic/claude-3.5-sonnet",    # Paid - user-facing
    "chat": "google/gemini-2.0-flash-exp:free",            # Free
    "embeddings": "openai/text-embedding-3-small"          # Cheap
}

# Fallback chain if primary fails
FALLBACK_MODELS = {
    "anthropic/claude-3.5-sonnet": "openai/gpt-4o",
    "google/gemini-2.0-flash-exp:free": "openai/gpt-4o-mini",
    "groq/llama-3.3-70b-versatile": "google/gemini-2.0-flash-exp:free"
}
```

**Recommended: Use OpenRouter as the unified gateway** — one API key gives access to all models, easy to swap, built-in fallbacks.

### 12.5 Cost Optimization Tactics

1. **Aggressive Caching** (biggest impact)
   - Cache full reports by ticker for 1 hour
   - Cache news scrapes for 30 min
   - Cache sentiment for 2 hours
   - Cache filings embeddings forever (filings don't change)
   - **Impact:** 60-70% cost reduction

2. **Token Limits**
   - Set `max_tokens` aggressively per agent
   - News agent summary: 200 tokens max
   - Report writer sections: 400 tokens each
   - Don't let LLMs ramble — costs scale linearly

3. **Batch Operations**
   - Sentiment: Send 50 Reddit posts in ONE prompt with structured output
   - Don't make 50 individual calls
   - **Impact:** 95% fewer API calls for sentiment

4. **Cheap Models for Routing**
   - Don't ask GPT-4o "what market is AAPL listed on?"
   - Use DB lookup or Gemini Flash for routing decisions

5. **Streaming for UX**
   - Stream Report Writer output token-by-token
   - User perceives speed even with slower models
   - Better than waiting 15s for full response

6. **Embed Once, Query Forever**
   - SEC filings change yearly, not hourly
   - Embed all filings on initial setup
   - Only re-embed when new filings drop
   - Embeddings are the cheapest part anyway

### 12.6 Phased Cost Estimate

**Phase 1 — Development (Weeks 1-4):** $0
- All free tiers (Gemini + Groq + OpenAI $5 credit)
- Limited testing volume, fits in free quotas

**Phase 2 — Demo Ready (Week 5+):** ~$5-10/month
- OpenRouter with $10 prepaid credit
- Per-agent model routing as defined above
- Caching enabled
- ~100-200 reports/month testing

**Phase 3 — If It Gets Real Users:** ~$20-50/month
- Direct Anthropic API for Report Writer (cheaper than OpenRouter at scale)
- Heavy caching mandatory
- Consider rate limits per user

### 12.7 Critical Honest Note

**Do NOT sacrifice quality on user-facing outputs to save $5/month.**

A bad Report Writer output makes the whole project look weak to recruiters. They'll read what Claude/GPT-4o writes — not the invisible News Agent output.

- **Free models for invisible backend agents** (sentiment classification, news scraping summaries) → smart
- **Free models for the user-facing report** → false economy, hurts your portfolio

Pay $0.02 per report for Claude Sonnet on the Report Writer. It's worth it.

### 12.8 Model Comparison Quick Reference

| Model | Strength | Weakness | Best For |
|---|---|---|---|
| Claude 3.5 Sonnet | Best reasoning, financial writing | Pricier | Report Writer, Portfolio Analyst |
| GPT-4o | Strong all-rounder, structured output | Pricier | Fallback for Sonnet |
| Gemini 2.0 Flash | Fast, huge context, free tier | Less nuanced reasoning | News summarization, chat |
| Llama 3.3 70B (Groq) | Insanely fast, free | Quality below GPT-4o | High-volume classification |
| DeepSeek V3 | Cheap, surprisingly capable | Newer, less reliable | Budget backup |
| Gemini 2.5 Pro | Strong reasoning, free tier | Lower rate limits | Complex tasks during dev |

---

## 13. Cost Estimate (Monthly)

| Service | Free Tier | Paid (if needed) |
|---|---|---|
| Vercel | Free | $20 if heavy |
| Railway | $5 credit free | ~$10-15 |
| Supabase | Free up to 500MB DB | $25 Pro |
| Upstash Redis | Free 10k cmds/day | $10 |
| Pinecone | Free 1M vectors | $70 (won't need yet) |
| OpenRouter | Pay-as-you-go | ~$10-30 with caching |
| Resend | Free 3k emails/mo | $20 |
| **Total** | **~$5-10** | **~$50-80** |

Very affordable to run as a portfolio project.

---

## 14. Key Design Decisions & Rationale

**Why LangGraph over CrewAI/AutoGen?**
- Better state management
- Native parallel execution
- Production-grade (used by LangChain team)
- Easier to debug

**Why OpenRouter over direct OpenAI?**
- One API key for GPT-4o, Claude, Gemini
- Easy A/B testing of models per agent (use cheaper Haiku for sentiment, GPT-4o for report writer)
- Better cost control

**Why FastAPI over Node.js backend?**
- Python ecosystem for AI/ML is unmatched
- LangChain/LangGraph are Python-first
- yfinance, snscrape, BeautifulSoup all Python

**Why Pinecone over pgvector?**
- You already have Pinecone experience
- Better filtering and metadata support
- Scales better

**Why Celery over alternatives?**
- Battle-tested, used at scale everywhere
- Great Python support
- Works perfectly with Redis as broker

---

## 15. What This Demonstrates To Recruiters

When this project is on the CV, it shows:

1. **Multi-agent AI orchestration** — the hottest skill in 2026
2. **Production RAG** — not a toy, real document QA at scale
3. **System design** — microservices, queues, caching, websockets
4. **Full-stack mastery** — Next.js to Pinecone, scrapers to LLMs
5. **Product thinking** — dual market strategy, user-centric features
6. **Domain expertise** — finance is high-value
7. **Ability to ship** — actual users using actual app

**Suggested CV bullet points:**
- *Built a production-grade multi-agent AI platform using LangGraph orchestrating 5 specialized agents (news, RAG, sentiment, technical, synthesis) for automated stock research on Pakistani (PSX) and US markets*
- *Implemented RAG pipeline over 1000+ SEC and PSX annual reports using Pinecone vector DB, enabling natural-language Q&A grounded in source filings*
- *Designed event-driven backend with FastAPI, Celery, and Redis handling real-time price updates, scheduled scrapers, and alert engine serving 100+ users*

---

## 16. Future Enhancements (v2+)

- **Urdu language support** — huge differentiator for Pakistani market
- **Mobile app** (React Native, share most code)
- **Voice mode** — "Hey StockSage, how's my watchlist today?"
- **Portfolio import** from brokers (Roshan Digital API if available)
- **Backtesting** — "If I had bought at every BUY signal, what would my return be?"
- **Premium tier** — unlimited reports, advanced alerts, API access
- **Community features** — share reports, discuss
- **More markets** — UK (LSE), India (NSE/BSE) etc

---

## 17. Notes for Claude Code (or any AI assistant) Reading This

<!--
  Development Guidelines:
  These are hard-won lessons for building this type of project.
  Follow these to avoid common pitfalls and ship faster.
-->

If you're building this in a new chat, here are pointers:

1. **Start with Phase 0 setup.** Don't skip to fancy features.
2. **Build agents in isolation first.** Each agent should be runnable standalone with a simple `python -m agents.news_agent ENGRO` for testing.
3. **The orchestrator is just glue.** Get individual agents working first, then connect them.
4. **PSX scraping is the hardest part.** Allow extra time. The PSX site changes layout occasionally.
5. **Use mock data in early phases.** Don't block frontend development on backend completion.
6. **Stream everything to frontend via WebSocket.** Reports take 10-30 seconds — make it feel fast with live progress.
7. **Cache aggressively.** LLM calls are expensive; news doesn't change every second.
8. **Test with cheap models first.** Use GPT-4o-mini for development, swap to GPT-4o for production.
9. **Add observability early.** Sentry + structured logging from day 1.
10. **Write the README as you go** — don't leave it for the end.

---

**END OF PLAN**

<!-- 
  Final Note:
  This plan is a living document. Update it as you learn more about
  what works and what doesn't. The best plan is one that adapts.
  
  Build with intent. Ship something real. Good luck.
-->
