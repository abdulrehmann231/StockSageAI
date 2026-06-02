"""Sentiment API — exposes the Sentiment Agent (plan § 4.7) over REST.

``GET /api/sentiment/{ticker}`` resolves the ticker's market from the stocks
table, runs the Sentiment Agent (Reddit + StockTwits + scraped Telegram/X), and
returns the scored crowd read. Results are Redis-cached (2h) by the agent;
``?refresh=true`` bypasses the cache for a fresh fetch.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from agents import sentiment_agent
from agents.sentiment_agent import SentimentResult
from core.deps import DbSession
from db.models import Stock

router = APIRouter(prefix="/api/sentiment", tags=["sentiment"])


@router.get("/{ticker}", response_model=SentimentResult)
async def get_stock_sentiment(
    ticker: str,
    db: DbSession,
    refresh: bool = Query(False, description="Bypass the cache and fetch fresh"),
) -> SentimentResult:
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    try:
        return await sentiment_agent.get_sentiment(
            stock.ticker,
            stock.market,
            company_name=stock.name,
            use_cache=not refresh,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Sentiment unavailable for {ticker}: {exc}",
        ) from exc
