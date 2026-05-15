from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from core.deps import DbSession
from db.models import Stock
from db.schemas import StockOut

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("", response_model=list[StockOut])
async def list_stocks(
    db: DbSession,
    market: str | None = Query(None, description="Filter by market: PSX | NYSE | NASDAQ"),
    limit: int = Query(5000, le=10000),
) -> list[StockOut]:
    stmt = select(Stock).where(Stock.is_active.is_(True))
    if market:
        stmt = stmt.where(Stock.market == market.upper())
    stmt = stmt.order_by(Stock.ticker).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return [StockOut.model_validate(r) for r in rows]


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input can't broaden the match."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/search", response_model=list[StockOut])
async def search_stocks(
    db: DbSession,
    q: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(20, le=50),
) -> list[StockOut]:
    pattern = f"%{_escape_like(q.lower())}%"
    stmt = (
        select(Stock)
        .where(
            Stock.is_active.is_(True),
            or_(
                Stock.ticker.ilike(pattern, escape="\\"),
                Stock.name.ilike(pattern, escape="\\"),
            ),
        )
        .order_by(Stock.ticker)
        .limit(limit)
    )
    rows = (await db.scalars(stmt)).all()
    return [StockOut.model_validate(r) for r in rows]


@router.get("/{ticker}", response_model=StockOut)
async def get_stock(ticker: str, db: DbSession) -> StockOut:
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")
    return StockOut.model_validate(stock)
