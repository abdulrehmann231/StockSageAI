"""Report API — exposes the Phase 5 orchestrator over REST.

``GET /api/report/{ticker}`` resolves the ticker's market/company from the
stocks table, runs the orchestrator (Price + News + Sentiment fan-out into the
Report Writer), and returns a single ``StockReport``. Results are Redis-cached
(30m) by the orchestrator; ``?refresh=true`` bypasses the cache for a fresh
synthesis.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from agents import orchestrator
from agents.report_writer import StockReport
from core.deps import DbSession
from db.models import Stock

router = APIRouter(prefix="/api/report", tags=["report"])


@router.get("/{ticker}", response_model=StockReport)
async def get_stock_report(
    ticker: str,
    db: DbSession,
    refresh: bool = Query(False, description="Bypass the cache and fetch fresh"),
    max_news_articles: int = Query(
        5,
        ge=1,
        le=20,
        description="Max news articles to consider when building the report",
    ),
) -> StockReport:
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    try:
        return await orchestrator.get_report(
            stock.ticker,
            stock.market,
            company_name=stock.name,
            use_cache=not refresh,
            max_news_articles=max_news_articles,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Report unavailable for {ticker}: {exc}",
        ) from exc
