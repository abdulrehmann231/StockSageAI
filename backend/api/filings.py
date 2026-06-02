"""Filings API — exposes the Filings RAG Agent (plan § 4.6) over REST.

``GET /api/filings/{ticker}`` resolves the ticker's market/company from the stocks
table, runs the Filings RAG agent (pre-filtered pgvector retrieval + grounded LLM
answers over indexed SEC/PSX filings), and returns structured per-question answers
with citations. Results are Redis-cached (6h) by the agent; ``?refresh=true``
forces a fresh run.

If a ticker has no indexed filings yet, the endpoint returns 200 with a
``chunks_indexed=0`` result whose answers explain that ingestion hasn't run —
this is a valid state, not an error.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from agents import filings_agent
from core.deps import DbSession
from db.models import Stock
from db.schemas import FilingsResult

router = APIRouter(prefix="/api/filings", tags=["filings"])


@router.get("/{ticker}", response_model=FilingsResult)
async def get_stock_filings(
    ticker: str,
    db: DbSession,
    refresh: bool = Query(False, description="Bypass the cache and recompute"),
) -> FilingsResult:
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    try:
        return await filings_agent.get_filings_analysis(
            stock.ticker,
            stock.market,
            company_name=stock.name,
            db=db,
            use_cache=not refresh,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Filings analysis unavailable for {ticker}: {exc}",
        ) from exc
