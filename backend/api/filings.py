"""Filings RAG API (Phase 4, plan § 4.6).

- ``POST /api/filings/{ticker}/index`` — fetch + chunk + embed + store filings.
- ``GET  /api/filings/{ticker}/status`` — what's indexed for the ticker.
- ``POST /api/filings/{ticker}/ask`` — grounded Q&A over the indexed filings.
- ``GET  /api/filings/{ticker}/analysis`` — the five auto key-question answers.

All routes are auth-gated. Ticker/market are resolved from the stocks table so
the agent always routes to the right source (SEC EDGAR vs PSX).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from agents import filings_agent
from core.deps import CurrentUser, DbSession
from db.models import Stock
from db.schemas import FilingsAnswer, FilingsAskRequest, FilingsData, FilingsIndexRequest
from services import filings_index, filings_store

router = APIRouter(prefix="/api/filings", tags=["filings"])


async def _resolve_stock(db: DbSession, ticker: str) -> Stock:
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker.strip().upper()))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")
    return stock


@router.post("/{ticker}/index")
async def index_filings(
    ticker: str,
    user: CurrentUser,
    db: DbSession,
    payload: FilingsIndexRequest | None = None,
) -> dict:
    """Fetch and index the ticker's latest filings into the vector store."""
    stock = await _resolve_stock(db, ticker)
    limit = payload.limit if payload else 1
    try:
        return await filings_index.index_ticker(
            db, ticker=stock.ticker, market=stock.market, limit=limit
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Indexing failed for {stock.ticker}: {exc}"
        ) from exc


@router.get("/{ticker}/status")
async def status_filings(ticker: str, user: CurrentUser, db: DbSession) -> dict:
    """Report how many filings/chunks are indexed for the ticker."""
    stock = await _resolve_stock(db, ticker)
    return await filings_store.filing_status(db, stock.ticker)


@router.post("/{ticker}/ask", response_model=FilingsAnswer)
async def ask_filings(
    ticker: str,
    payload: FilingsAskRequest,
    user: CurrentUser,
    db: DbSession,
) -> FilingsAnswer:
    """Answer a grounded question over the ticker's indexed filings."""
    stock = await _resolve_stock(db, ticker)
    try:
        return await filings_agent.answer_question(
            db,
            ticker=stock.ticker,
            market=stock.market,
            question=payload.question,
            k=payload.k,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Filings Q&A failed for {stock.ticker}: {exc}"
        ) from exc


@router.get("/{ticker}/analysis", response_model=FilingsData)
async def analysis_filings(ticker: str, user: CurrentUser, db: DbSession) -> FilingsData:
    """Compile answers to the five auto key questions for the ticker."""
    stock = await _resolve_stock(db, ticker)
    try:
        return await filings_agent.auto_analysis(
            db, ticker=stock.ticker, market=stock.market
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Filings analysis failed for {stock.ticker}: {exc}"
        ) from exc
