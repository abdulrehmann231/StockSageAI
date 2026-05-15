from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from agents import price_agent
from core.deps import DbSession
from db.models import Stock
from db.schemas import PriceQuote

router = APIRouter(prefix="/api/stocks", tags=["prices"])


@router.get("/{ticker}/price", response_model=PriceQuote)
async def get_stock_price(ticker: str, db: DbSession) -> PriceQuote:
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    try:
        return await price_agent.get_price(stock.ticker, stock.market)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Upstream data unavailable for {ticker}: {exc}",
        ) from exc
