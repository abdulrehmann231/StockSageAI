"""Watchlist API (plan § 4.10).

Lets an authenticated user track a set of tickers. Each row is the composite
``(user_id, ticker)``; ``POST`` is idempotent (adding a ticker that's already
in the list is a 200 with the existing row rather than a 409) so the obvious
"click the star twice" UI doesn't surface confusing errors.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select

from core.deps import CurrentUser, DbSession
from db.models import Stock, WatchlistItem
from db.schemas import WatchlistAddRequest, WatchlistItemOut

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItemOut])
async def list_watchlist(user: CurrentUser, db: DbSession) -> list[WatchlistItemOut]:
    """Return the current user's watched stocks ordered by most-recently-added."""
    stmt = (
        select(
            Stock.ticker,
            Stock.name,
            Stock.market,
            Stock.sector,
            Stock.currency,
            WatchlistItem.added_at,
        )
        .join(WatchlistItem, WatchlistItem.ticker == Stock.ticker)
        .where(WatchlistItem.user_id == user.id)
        .order_by(WatchlistItem.added_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        WatchlistItemOut(
            ticker=row.ticker,
            name=row.name,
            market=row.market,
            sector=row.sector,
            currency=row.currency,
            added_at=row.added_at,
        )
        for row in rows
    ]


@router.post("", response_model=WatchlistItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_watchlist(
    payload: WatchlistAddRequest,
    user: CurrentUser,
    db: DbSession,
    response: Response,
) -> WatchlistItemOut:
    """Add a ticker to the user's watchlist.

    Idempotent: if the row already exists, returns 200 + the existing row.
    """
    ticker = payload.ticker.strip().upper()
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    existing = await db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.ticker == ticker,
        )
    )
    if existing:
        response.status_code = status.HTTP_200_OK
        return WatchlistItemOut(
            ticker=stock.ticker,
            name=stock.name,
            market=stock.market,
            sector=stock.sector,
            currency=stock.currency,
            added_at=existing.added_at,
        )

    item = WatchlistItem(user_id=user.id, ticker=ticker)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return WatchlistItemOut(
        ticker=stock.ticker,
        name=stock.name,
        market=stock.market,
        sector=stock.sector,
        currency=stock.currency,
        added_at=item.added_at,
    )


@router.delete("/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_watchlist(
    ticker: str,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    """Remove a ticker from the user's watchlist; 404 if it wasn't on it."""
    ticker = ticker.strip().upper()
    result = await db.execute(
        delete(WatchlistItem).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.ticker == ticker,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{ticker} is not on your watchlist"
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
