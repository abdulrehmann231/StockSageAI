"""News API — exposes the News Agent (plan § 4.5) over REST.

``GET /api/news/{ticker}`` resolves the ticker's market/company from the stocks
table, runs the News Agent (PSX scrapers for Pakistani tickers; Yahoo/NewsAPI/
Google News for global), and returns ranked, impact-classified articles. Results
are Redis-cached (30m) by the agent; ``?refresh=true`` forces a fresh fetch and
``?limit=`` caps the number of articles returned.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from agents import news_agent
from agents.news_agent import DEFAULT_MAX_ARTICLES, NewsResult
from core.deps import DbSession
from db.models import Stock

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("/{ticker}", response_model=NewsResult)
async def get_stock_news(
    ticker: str,
    db: DbSession,
    refresh: bool = Query(False, description="Bypass the cache and fetch fresh"),
    limit: int = Query(
        DEFAULT_MAX_ARTICLES, ge=1, le=20, description="Max articles to return"
    ),
) -> NewsResult:
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    try:
        return await news_agent.get_news(
            stock.ticker,
            stock.market,
            company_name=stock.name,
            max_articles=limit,
            use_cache=not refresh,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"News unavailable for {ticker}: {exc}",
        ) from exc
