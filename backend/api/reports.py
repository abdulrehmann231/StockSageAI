"""Reports persistence API (plan § 4.9 / § 7).

Layered on top of the Phase-5 orchestrator: ``POST /api/reports/generate`` runs
the agent fan-out and **persists** the resulting ``StockReport`` so the
chat-with-stock feature can answer follow-up questions without re-running every
agent. The Phase-5 ``GET /api/report/{ticker}`` endpoint remains for one-shot,
ticker-keyed reads; this router adds id-keyed persistence + listing.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agents import orchestrator
from core.deps import CurrentUser, DbSession
from db.models import Report, Stock
from db.schemas import ReportDetailOut, ReportGenerateRequest, ReportRecordOut

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/generate", response_model=ReportDetailOut, status_code=status.HTTP_201_CREATED)
async def generate_report(
    payload: ReportGenerateRequest,
    user: CurrentUser,
    db: DbSession,
) -> ReportDetailOut:
    """Run the orchestrator and persist a Report row for the current user."""
    ticker = payload.ticker.strip().upper()
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    try:
        report = await orchestrator.get_report(
            stock.ticker,
            stock.market,
            company_name=stock.name,
            use_cache=not payload.refresh,
            max_news_articles=payload.max_news_articles,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Report generation failed for {ticker}: {exc}",
        ) from exc

    record = Report(
        user_id=user.id,
        ticker=stock.ticker,
        market=stock.market,
        verdict=report.verdict,
        confidence=report.confidence,
        composite_score=report.composite_score,
        report_data=report.model_dump(mode="json"),
    )
    db.add(record)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Could not persist report: {exc.orig}",
        ) from exc
    await db.refresh(record)
    return _to_detail(record)


@router.get("/user", response_model=list[ReportRecordOut])
async def list_user_reports(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(20, ge=1, le=100),
) -> list[ReportRecordOut]:
    """Return the current user's most recent persisted reports (slim view)."""
    stmt = (
        select(Report)
        .where(Report.user_id == user.id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    rows = (await db.scalars(stmt)).all()
    return [ReportRecordOut.model_validate(row) for row in rows]


@router.get("/{report_id}", response_model=ReportDetailOut)
async def get_report_detail(
    report_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
) -> ReportDetailOut:
    """Return one persisted Report owned by the current user."""
    record = await db.scalar(
        select(Report).where(Report.id == report_id, Report.user_id == user.id)
    )
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return _to_detail(record)


def _to_detail(record: Report) -> ReportDetailOut:
    return ReportDetailOut(
        id=record.id,
        ticker=record.ticker,
        market=record.market,
        verdict=record.verdict,
        confidence=record.confidence,
        composite_score=float(record.composite_score)
        if record.composite_score is not None
        else None,
        report_data=record.report_data,
        created_at=record.created_at,
    )
