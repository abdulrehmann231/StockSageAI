"""Daily portfolio snapshot worker (plan § 4.15.7).

For every user that holds at least one active position, compute the current
portfolio value and upsert a ``portfolio_snapshots`` row for today. These rows
power the performance-over-time chart.

``run_portfolio_snapshots(db)`` is callable directly from an async context —
wiring it into Celery beat (run daily after PSX + NYSE close) is a deployment
concern, not a worker concern. Tests drive it directly with seeded holdings.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Holding, PortfolioSnapshot, Stock
from db.session import SessionLocal
from services import portfolio_service

logger = logging.getLogger(__name__)


async def run_portfolio_snapshots(
    db: AsyncSession | None = None,
    *,
    snapshot_date: date | None = None,
    use_cache: bool = True,
) -> int:
    """Upsert today's snapshot for each user with active holdings.

    Returns the number of snapshots written/updated. Per-user failures are
    isolated and logged so one bad portfolio never sinks the whole sweep.
    """
    owns_session = db is None
    session = db or SessionLocal()
    today = snapshot_date or datetime.now(timezone.utc).date()
    written = 0

    try:
        user_ids = (
            await session.scalars(
                select(Holding.user_id)
                .where(Holding.is_active.is_(True))
                .distinct()
            )
        ).all()

        for user_id in user_ids:
            try:
                rows = await _load_active_rows(session, user_id)
                holdings, metrics, _errors = await portfolio_service.build_portfolio(
                    rows, use_cache=use_cache
                )
                if metrics.priced_count == 0:
                    continue
                await _upsert_snapshot(session, user_id, today, holdings, metrics)
                written += 1
            except Exception as exc:  # noqa: BLE001
                logger.info("Snapshot failed for user %s: %s", user_id, exc)

        await session.commit()
    finally:
        if owns_session:
            await session.close()

    return written


async def _load_active_rows(session: AsyncSession, user_id) -> list[tuple[Holding, Stock]]:
    stmt = (
        select(Holding, Stock)
        .join(Stock, Stock.ticker == Holding.ticker)
        .where(Holding.user_id == user_id, Holding.is_active.is_(True))
    )
    return [(h, s) for h, s in (await session.execute(stmt)).all()]


async def _upsert_snapshot(
    session: AsyncSession,
    user_id,
    snapshot_date: date,
    holdings,
    metrics,
) -> None:
    existing = await session.scalar(
        select(PortfolioSnapshot).where(
            PortfolioSnapshot.user_id == user_id,
            PortfolioSnapshot.snapshot_date == snapshot_date,
        )
    )
    breakdown = portfolio_service.snapshot_breakdown(holdings)
    if existing:
        existing.total_value = metrics.total_value
        existing.total_cost_basis = metrics.total_cost_basis
        existing.total_gain_loss = metrics.total_gain_loss
        existing.breakdown = breakdown
    else:
        session.add(
            PortfolioSnapshot(
                user_id=user_id,
                total_value=metrics.total_value,
                total_cost_basis=metrics.total_cost_basis,
                total_gain_loss=metrics.total_gain_loss,
                snapshot_date=snapshot_date,
                breakdown=breakdown,
            )
        )
