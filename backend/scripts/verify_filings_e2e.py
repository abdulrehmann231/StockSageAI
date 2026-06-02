"""End-to-end smoke test for the Filings RAG pipeline (plan § 4.6).

Builds a live index for one or more tickers and runs the agent against it:

    SEC EDGAR (real 10-K/10-Q)  ──► fetch_filing_text ──► segment ──► chunk
        ──► embed (local) ──► pgvector upsert ──► similarity_search ──► answers

Usage (from backend/, with the venv active and Postgres+Redis running)::

    python -m scripts.verify_filings_e2e AAPL:GLOBAL MSFT:GLOBAL ENGRO:PSX

Notes:
- Without ``OPENROUTER_API_KEY`` set, answers use the deterministic *extractive*
  fallback (real filing text + citations) rather than synthesized prose — that
  still proves the retrieval path end-to-end.
- Without ``sentence-transformers``/torch installed, embeddings use the
  deterministic hashing fallback; retrieval still returns the right ticker's
  chunks (pre-filtered by SQL) but ranking is weaker than bge-small.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from agents import filings_agent
from db.models import Stock
from db.session import Base, SessionLocal, engine
from ingestion import pipeline
from services import embeddings, vector_store


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
        await conn.run_sync(Base.metadata.create_all)


async def _seed_stock(db, ticker: str, market: str) -> None:
    existing = await db.get(Stock, ticker)
    if not existing:
        db.add(Stock(ticker=ticker, name=f"{ticker} (e2e)", market=market))
        await db.commit()


async def _run_one(ticker: str, market: str) -> None:
    print(f"\n{'='*70}\n{ticker} ({market})\n{'='*70}")
    async with SessionLocal() as db:
        await _seed_stock(db, ticker, market)

        if market == "GLOBAL":
            n = await pipeline.ingest_global_ticker(db, ticker, max_filings=1)
        else:
            n = await pipeline.ingest_psx_ticker(db, ticker, max_reports=1)
        print(f"  ingested chunks: {n}")

        if n == 0:
            print("  (no chunks — source discovery/extraction returned nothing)")
            return

        # Prove pre-filtered retrieval works directly.
        hits = await vector_store.similarity_search(
            db, query="What is the revenue trend?", ticker=ticker, top_k=3
        )
        print(f"  retrieval sanity: {len(hits)} chunks for '{ticker}' "
              f"(top distance={hits[0].distance:.4f})" if hits else "  retrieval: 0 hits")

        result = await filings_agent.get_filings_analysis(
            ticker, market, db=db, use_cache=False
        )
        print(f"  chunks_indexed reported by agent: {result.chunks_indexed}")
        for ans in result.answers:
            print(f"\n  Q: {ans.question}")
            print(f"  A: {ans.answer[:320]}{'…' if len(ans.answer) > 320 else ''}")
            if ans.citations:
                c = ans.citations[0]
                cite = f"{c.filing_type}"
                if c.fiscal_year:
                    cite += f" FY{c.fiscal_year}"
                if c.section:
                    cite += f" / {c.section}"
                if c.page:
                    cite += f" p.{c.page}"
                print(f"     ↳ cite: {cite}  (dist={c.distance})  [{len(ans.citations)} sources]")


async def _main(pairs: list[str]) -> None:
    print(f"embeddings backend: {'bge-small (real)' if embeddings.using_real_model() else 'hashing fallback'}")
    await _ensure_schema()
    for pair in pairs:
        ticker, _, market = pair.partition(":")
        await _run_one(ticker.upper(), (market or "GLOBAL").upper())

    # Demonstrate cross-ticker pre-filter isolation when >1 ticker was indexed.
    global_tickers = [p.partition(":")[0].upper() for p in pairs]
    if len(global_tickers) > 1:
        async with SessionLocal() as db:
            probe = global_tickers[0]
            hits = await vector_store.similarity_search(
                db, query="revenue and profit", ticker=probe, top_k=10
            )
            tickers_returned = {h.ticker for h in hits}
            print(f"\n{'='*70}\nPre-filter isolation check\n{'='*70}")
            print(f"  query scoped to {probe} → tickers in results: {tickers_returned or '∅'}")
            print(f"  isolation {'OK' if tickers_returned <= {probe} else 'LEAK!'}")

    from services import cache_service

    await cache_service.close()
    await engine.dispose()


if __name__ == "__main__":
    args = sys.argv[1:] or ["AAPL:GLOBAL"]
    asyncio.run(_main(args))
