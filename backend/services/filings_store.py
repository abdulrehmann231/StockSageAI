"""pgvector-backed store for filing chunks (Phase 4).

Thin data-access layer over the ``filings`` / ``filing_chunks`` tables. Kept
free of HTTP/scraping concerns so it can be unit-tested directly against the DB
with deterministic embeddings.

Retrieval uses pgvector's cosine distance. Because every embedding is L2-
normalised at write time (see ``embedding_service._normalize``), cosine distance
is a clean similarity metric and ``1 - distance`` is a usable similarity score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Filing, FilingChunk

logger = logging.getLogger(__name__)


@dataclass
class ChunkInput:
    content: str
    chunk_index: int
    section: str | None = None
    page: int | None = None


@dataclass
class RetrievedChunk:
    content: str
    section: str | None
    page: int | None
    chunk_index: int
    filing_type: str | None
    fiscal_year: int | None
    url: str | None
    similarity: float


async def upsert_filing(
    db: AsyncSession,
    *,
    ticker: str,
    market: str,
    source: str,
    external_id: str | None,
    filing_type: str | None = None,
    fiscal_year: int | None = None,
    title: str | None = None,
    url: str | None = None,
) -> Filing:
    """Find-or-create a Filing row on its ``(ticker, source, external_id)`` identity."""
    existing = await db.scalar(
        select(Filing).where(
            Filing.ticker == ticker,
            Filing.source == source,
            Filing.external_id == external_id,
        )
    )
    if existing:
        existing.market = market
        existing.filing_type = filing_type
        existing.fiscal_year = fiscal_year
        existing.title = title
        existing.url = url
        return existing

    filing = Filing(
        ticker=ticker,
        market=market,
        source=source,
        external_id=external_id,
        filing_type=filing_type,
        fiscal_year=fiscal_year,
        title=title,
        url=url,
    )
    db.add(filing)
    await db.flush()
    return filing


async def replace_chunks(
    db: AsyncSession,
    filing: Filing,
    chunks: Sequence[ChunkInput],
    embeddings: Sequence[Sequence[float]],
) -> int:
    """Replace a filing's chunks with a fresh embedded set (idempotent re-index)."""
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings length mismatch")

    await db.execute(delete(FilingChunk).where(FilingChunk.filing_id == filing.id))

    for chunk, embedding in zip(chunks, embeddings):
        db.add(
            FilingChunk(
                filing_id=filing.id,
                ticker=filing.ticker,
                market=filing.market,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                section=chunk.section,
                page=chunk.page,
                embedding=list(embedding),
            )
        )
    filing.chunk_count = len(chunks)
    return len(chunks)


async def search(
    db: AsyncSession,
    *,
    ticker: str,
    query_embedding: Sequence[float],
    k: int = 5,
) -> list[RetrievedChunk]:
    """Return the ``k`` most similar chunks for a ticker, nearest first."""
    distance = FilingChunk.embedding.cosine_distance(list(query_embedding))
    stmt = (
        select(
            FilingChunk.content,
            FilingChunk.section,
            FilingChunk.page,
            FilingChunk.chunk_index,
            Filing.filing_type,
            Filing.fiscal_year,
            Filing.url,
            distance.label("distance"),
        )
        .join(Filing, Filing.id == FilingChunk.filing_id)
        .where(FilingChunk.ticker == ticker)
        .order_by(distance.asc())
        .limit(k)
    )
    rows = (await db.execute(stmt)).all()
    return [
        RetrievedChunk(
            content=row.content,
            section=row.section,
            page=row.page,
            chunk_index=row.chunk_index,
            filing_type=row.filing_type,
            fiscal_year=row.fiscal_year,
            url=row.url,
            similarity=round(1.0 - float(row.distance), 4),
        )
        for row in rows
    ]


async def filing_status(db: AsyncSession, ticker: str) -> dict:
    """Summary of what's indexed for a ticker (for the API status endpoint)."""
    filings = (
        await db.scalars(
            select(Filing).where(Filing.ticker == ticker).order_by(Filing.indexed_at.desc())
        )
    ).all()
    total_chunks = (
        await db.scalar(
            select(func.coalesce(func.sum(Filing.chunk_count), 0)).where(
                Filing.ticker == ticker
            )
        )
    ) or 0
    return {
        "ticker": ticker,
        "filing_count": len(filings),
        "chunk_count": int(total_chunks),
        "filings": [
            {
                "id": str(f.id),
                "source": f.source,
                "filing_type": f.filing_type,
                "fiscal_year": f.fiscal_year,
                "title": f.title,
                "url": f.url,
                "chunk_count": f.chunk_count,
                "indexed_at": f.indexed_at.isoformat() if f.indexed_at else None,
            }
            for f in filings
        ],
    }
