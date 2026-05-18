"""Stock listing and search endpoints.

Includes pagination for the main list endpoint and search functionality.
"""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from core.deps import DbSession
from db.models import Stock
from db.schemas import PaginatedStocks, PaginationMeta, StockOut

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("", response_model=PaginatedStocks)
async def list_stocks(
    db: DbSession,
    market: str | None = Query(None, description="Filter by market: PSX | NYSE | NASDAQ"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(50, ge=1, le=500, description="Items per page"),
) -> PaginatedStocks:
    """List all active stocks with pagination.

    Returns paginated results with metadata including total count,
    current page, and navigation flags.
    """
    # Build base query
    base_query = select(Stock).where(Stock.is_active.is_(True))
    if market:
        base_query = base_query.where(Stock.market == market.upper())

    # Get total count
    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = await db.scalar(count_stmt) or 0

    # Calculate pagination
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    offset = (page - 1) * per_page

    # Fetch page of results
    stmt = base_query.order_by(Stock.ticker).offset(offset).limit(per_page)
    rows = (await db.scalars(stmt)).all()

    return PaginatedStocks(
        items=[StockOut.model_validate(r) for r in rows],
        meta=PaginationMeta(
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input can't broaden the match."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/search", response_model=list[StockOut])
async def search_stocks(
    db: DbSession,
    q: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(20, le=50),
) -> list[StockOut]:
    """Search stocks by ticker or company name.

    Uses case-insensitive partial matching. Results are limited
    for performance (use for autocomplete, not full exports).
    """
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
    """Get a single stock by ticker symbol."""
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker.upper()))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")
    return StockOut.model_validate(stock)
