"""Portfolio Tracker API (plan § 4.15).

Holdings CRUD (each add auto-logs a BUY transaction), live P&L, aggregate
metrics, transaction history + CSV export, historical performance from daily
snapshots, an AI rebalancing analysis (Portfolio Analyst Agent), and a
tax-liability estimate. Every surface is auth-gated and user-scoped — a user
can only ever see/mutate their own rows.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, select

from agents import portfolio_analyst_agent
from core.deps import CurrentUser, DbSession
from db.models import (
    Holding,
    PortfolioAnalysis,
    PortfolioSnapshot,
    Stock,
    Transaction,
)
from db.schemas import (
    HoldingCreateRequest,
    HoldingOut,
    HoldingUpdateRequest,
    PerformanceOut,
    PerformancePoint,
    PortfolioAnalysisOut,
    PortfolioMetrics,
    PortfolioOut,
    TaxEstimateOut,
    TransactionCreateRequest,
    TransactionOut,
)
from services import portfolio_service

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


_RANGE_DAYS = {"30d": 30, "90d": 90, "1y": 365, "all": None}


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #


async def _load_active_rows(db: DbSession, user_id: uuid.UUID) -> list[tuple[Holding, Stock]]:
    """Load the user's active holdings joined to their Stock row."""
    stmt = (
        select(Holding, Stock)
        .join(Stock, Stock.ticker == Holding.ticker)
        .where(Holding.user_id == user_id, Holding.is_active.is_(True))
        .order_by(Holding.created_at.desc())
    )
    return [(h, s) for h, s in (await db.execute(stmt)).all()]


# --------------------------------------------------------------------------- #
# Portfolio view + metrics
# --------------------------------------------------------------------------- #


@router.get("", response_model=PortfolioOut)
async def get_portfolio(
    user: CurrentUser,
    db: DbSession,
    refresh: bool = Query(False),
) -> PortfolioOut:
    """Full portfolio with live per-holding P&L and aggregate metrics."""
    rows = await _load_active_rows(db, user.id)
    holdings, metrics, errors = await portfolio_service.build_portfolio(
        rows, use_cache=not refresh
    )
    return PortfolioOut(
        holdings=holdings,
        metrics=metrics,
        errors=errors,
        fetched_at=datetime.now(timezone.utc),
    )


@router.get("/metrics", response_model=PortfolioMetrics)
async def get_metrics(
    user: CurrentUser,
    db: DbSession,
    refresh: bool = Query(False),
) -> PortfolioMetrics:
    """Aggregate, portfolio-wide metrics only."""
    rows = await _load_active_rows(db, user.id)
    _holdings, metrics, _errors = await portfolio_service.build_portfolio(
        rows, use_cache=not refresh
    )
    return metrics


@router.get("/performance", response_model=PerformanceOut)
async def get_performance(
    user: CurrentUser,
    db: DbSession,
    range: str = Query("30d"),
) -> PerformanceOut:
    """Historical portfolio value from daily snapshots, for the chart."""
    range_key = range.lower()
    if range_key not in _RANGE_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"range must be one of {sorted(_RANGE_DAYS)}",
        )

    stmt = select(PortfolioSnapshot).where(PortfolioSnapshot.user_id == user.id)
    days = _RANGE_DAYS[range_key]
    if days is not None:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
        stmt = stmt.where(PortfolioSnapshot.snapshot_date >= cutoff)
    stmt = stmt.order_by(PortfolioSnapshot.snapshot_date.asc())

    snapshots = (await db.scalars(stmt)).all()
    points = [
        PerformancePoint(
            snapshot_date=s.snapshot_date,
            total_value=float(s.total_value),
            total_cost_basis=float(s.total_cost_basis),
            total_gain_loss=float(s.total_gain_loss),
        )
        for s in snapshots
    ]
    return PerformanceOut(range=range_key, points=points)


# --------------------------------------------------------------------------- #
# Holdings CRUD
# --------------------------------------------------------------------------- #


@router.post("/holdings", response_model=HoldingOut, status_code=status.HTTP_201_CREATED)
async def add_holding(
    payload: HoldingCreateRequest,
    user: CurrentUser,
    db: DbSession,
) -> HoldingOut:
    """Add a new holding and auto-log the matching BUY transaction."""
    ticker = payload.ticker.strip().upper()
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    holding = Holding(
        user_id=user.id,
        ticker=ticker,
        quantity=payload.quantity,
        avg_buy_price=payload.avg_buy_price,
        buy_date=payload.buy_date,
        notes=payload.notes,
    )
    db.add(holding)
    await db.flush()

    db.add(
        Transaction(
            user_id=user.id,
            holding_id=holding.id,
            ticker=ticker,
            transaction_type="BUY",
            quantity=payload.quantity,
            price=payload.avg_buy_price,
            transaction_date=payload.buy_date or datetime.now(timezone.utc).date(),
            notes=payload.notes,
        )
    )
    await db.commit()
    await db.refresh(holding)
    return portfolio_service._enrich_one(holding, stock, None)


@router.patch("/holdings/{holding_id}", response_model=HoldingOut)
async def update_holding(
    holding_id: uuid.UUID,
    payload: HoldingUpdateRequest,
    user: CurrentUser,
    db: DbSession,
) -> HoldingOut:
    """Update one of the user's holdings (partial)."""
    holding = await db.scalar(
        select(Holding).where(Holding.id == holding_id, Holding.user_id == user.id)
    )
    if not holding:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Holding not found")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(holding, field, value)
    await db.commit()
    await db.refresh(holding)

    stock = await db.scalar(select(Stock).where(Stock.ticker == holding.ticker))
    return portfolio_service._enrich_one(holding, stock, None)


@router.delete("/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holding(
    holding_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    """Remove one of the user's holdings."""
    result = await db.execute(
        delete(Holding).where(Holding.id == holding_id, Holding.user_id == user.id)
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Holding not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #


@router.post(
    "/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED
)
async def add_transaction(
    payload: TransactionCreateRequest,
    user: CurrentUser,
    db: DbSession,
) -> TransactionOut:
    """Log a manual BUY/SELL transaction."""
    ticker = payload.ticker.strip().upper()
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    txn = Transaction(
        user_id=user.id,
        ticker=ticker,
        transaction_type=payload.transaction_type,
        quantity=payload.quantity,
        price=payload.price,
        transaction_date=payload.transaction_date,
        fees=payload.fees,
        notes=payload.notes,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return TransactionOut.model_validate(txn)


def _transactions_query(user_id: uuid.UUID, ticker: str | None, market: str | None):
    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc())
    )
    if ticker:
        stmt = stmt.where(Transaction.ticker == ticker.strip().upper())
    if market:
        stmt = stmt.join(Stock, Stock.ticker == Transaction.ticker).where(
            Stock.market == market.strip().upper()
        )
    return stmt


@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(
    user: CurrentUser,
    db: DbSession,
    ticker: str | None = Query(None),
    market: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> list[TransactionOut]:
    """Transaction history, newest-first, with optional ticker/market filters."""
    stmt = _transactions_query(user.id, ticker, market).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return [TransactionOut.model_validate(r) for r in rows]


@router.get("/transactions/export.csv")
async def export_transactions_csv(
    user: CurrentUser,
    db: DbSession,
    ticker: str | None = Query(None),
    market: str | None = Query(None),
) -> Response:
    """Export the user's transaction history as a CSV (for tax/accounting)."""
    stmt = _transactions_query(user.id, ticker, market)
    rows = (await db.scalars(stmt)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["transaction_date", "ticker", "type", "quantity", "price", "fees", "notes"]
    )
    for r in rows:
        writer.writerow(
            [
                r.transaction_date.isoformat(),
                r.ticker,
                r.transaction_type,
                r.quantity,
                r.price,
                r.fees,
                (r.notes or "").replace("\n", " "),
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="transactions.csv"'},
    )


# --------------------------------------------------------------------------- #
# Tax estimate
# --------------------------------------------------------------------------- #


@router.get("/tax-estimate", response_model=TaxEstimateOut)
async def tax_estimate(
    user: CurrentUser,
    db: DbSession,
    refresh: bool = Query(False),
) -> TaxEstimateOut:
    """Estimate capital-gains tax liability if all active holdings sold today."""
    rows = await _load_active_rows(db, user.id)
    return await portfolio_service.estimate_tax(rows, use_cache=not refresh)


# --------------------------------------------------------------------------- #
# AI analysis
# --------------------------------------------------------------------------- #


@router.post("/analyze", response_model=PortfolioAnalysisOut, status_code=status.HTTP_201_CREATED)
async def analyze(
    user: CurrentUser,
    db: DbSession,
    refresh: bool = Query(False),
) -> PortfolioAnalysisOut:
    """Run the Portfolio Analyst Agent and persist the analysis."""
    rows = await _load_active_rows(db, user.id)
    if not rows:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No active holdings to analyze — add a holding first.",
        )

    holdings, metrics, _errors = await portfolio_service.build_portfolio(
        rows, use_cache=not refresh
    )
    try:
        analysis = await portfolio_analyst_agent.analyze_portfolio(
            holdings, metrics, risk_profile=user.risk_profile
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Portfolio analysis failed: {exc}"
        ) from exc

    record = PortfolioAnalysis(
        user_id=user.id,
        health_score=int(analysis.get("health_score", 0)),
        analysis_data=analysis,
        recommendations={"items": analysis.get("recommendations", [])},
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return PortfolioAnalysisOut.model_validate(record)


@router.get("/analyses/latest", response_model=PortfolioAnalysisOut)
async def latest_analysis(
    user: CurrentUser,
    db: DbSession,
) -> PortfolioAnalysisOut:
    """Return the user's most recent persisted portfolio analysis."""
    record = await db.scalar(
        select(PortfolioAnalysis)
        .where(PortfolioAnalysis.user_id == user.id)
        .order_by(PortfolioAnalysis.created_at.desc())
        .limit(1)
    )
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No analysis found")
    return PortfolioAnalysisOut.model_validate(record)
