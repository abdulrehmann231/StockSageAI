# StockSage AI

> Multi-agent AI stock research analyst for Pakistani (PSX) and Global (US) markets.

A production-grade platform that orchestrates specialized AI agents — price data, news, SEC/PSX filings (RAG), sentiment, portfolio analysis — to generate investment reports with a Buy/Hold/Sell verdict.

See [`plan.md`](./plan.md) for the full architecture, feature spec, and phased build plan.

## Stack
- **Frontend:** Next.js 14, Tailwind, shadcn/ui, Zustand, TanStack Query
- **Backend:** FastAPI, LangGraph, Celery
- **Data:** PostgreSQL (Supabase), Pinecone, Redis (Upstash)
- **LLMs:** OpenRouter gateway — Claude Sonnet, Gemini Flash, Llama 3.3 (Groq)

## Repository Layout
```
stocksage-ai/
├── frontend/           # Next.js 14 app
├── backend/            # FastAPI + agents + workers
├── scripts/            # One-off data scripts
├── docker-compose.yml  # Local Postgres + Redis
└── plan.md             # Full project plan
```

## Quick Start

### Prerequisites
- Node.js 20+
- Python 3.11+
- Docker (for local Postgres + Redis)

### Local Dev
```bash
# Start Postgres + Redis
docker compose up -d

# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env            # fill in keys
uvicorn main:app --reload

# Frontend (new terminal)
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

## Build Status
Tracked in `plan.md` § 10 (Phased Build Plan). Each phase ships on its own feature branch.

## Notes

### Playwright (Phase 2+)
The PSX price agent uses Playwright/Chromium. After `pip install -r requirements.txt`,
run once:
```bash
python -m playwright install chromium
```

### Migrations
For local dev the backend auto-creates tables via SQLAlchemy `Base.metadata.create_all`
on startup. **Before production deploy, switch to Alembic-managed migrations** —
`alembic` is already pinned in `requirements.txt`. Initialize with
`alembic init alembic`, then generate revisions with
`alembic revision --autogenerate -m "..."`.

### Tests
The backend test suite reads `backend/.env.test`. Create the test database once with
`psql -U postgres -c "CREATE DATABASE stocksage_test OWNER stocksage;"` and run
`pytest` from `backend/`.

## License
TBD
