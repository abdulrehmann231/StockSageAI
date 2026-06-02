"""pgvector-backed similarity search for the Filings RAG agent.

This module is the concrete answer to "can I pre-filter?" — yes. Because the
embeddings live in a normal Postgres table (``filing_chunks``) alongside plain
metadata columns, every retrieval is a single SQL statement that:

1. **Pre-filters** on real columns — ``ticker`` (always), and optionally
   ``filing_type`` / ``fiscal_year`` / ``market`` — via a ``WHERE`` clause.
2. **Ranks** the surviving rows by ``embedding <=> query`` (pgvector cosine
   distance) and takes the top-K.

For a single-ticker query the filtered set is small (a handful of filings), so an
exact distance scan is fast and 100% accurate — no ANN approximation needed at
this scale. An HNSW index can be added later for large corpora; see
``ensure_vector_index``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FilingChunk
from services import embeddings

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5


@dataclass(slots=True)
class RetrievedChunk:
    """A chunk returned from similarity search, with its distance score."""

    content: str
    ticker: str
    market: str
    filing_type: str
    fiscal_year: int | None
    section: str | None
    page: int | None
    source_url: str | None
    distance: float


# --------------------------------------------------------------------------- #
# Write path                                                                  #
# --------------------------------------------------------------------------- #


async def upsert_chunks(
    db: AsyncSession,
    *,
    ticker: str,
    market: str,
    chunks: list[dict],
    replace_existing: bool = True,
) -> int:
    """Embed and persist filing chunks for a ticker.

    Each ``chunk`` dict carries: ``content`` (required), and optionally
    ``filing_type``, ``fiscal_year``, ``section``, ``page``, ``source_url``,
    ``chunk_index``. Embeddings are computed locally in one batch.

    When ``replace_existing`` is True, all prior chunks for the ticker are deleted
    first so re-ingestion is idempotent (no duplicate rows on a weekly refresh).
    Returns the number of chunks written.
    """
    ticker = ticker.upper()
    if not chunks:
        return 0

    if replace_existing:
        await db.execute(sql_delete(FilingChunk).where(FilingChunk.ticker == ticker))

    contents = [c["content"] for c in chunks]
    vectors = embeddings.embed_texts(contents)

    rows = [
        FilingChunk(
            ticker=ticker,
            market=market,
            filing_type=c.get("filing_type", "unknown"),
            fiscal_year=c.get("fiscal_year"),
            section=c.get("section"),
            page=c.get("page"),
            source_url=c.get("source_url"),
            chunk_index=c.get("chunk_index", i),
            content=c["content"],
            embedding=vec,
        )
        for i, (c, vec) in enumerate(zip(chunks, vectors))
    ]
    db.add_all(rows)
    await db.commit()
    logger.info("Upserted %d filing chunks for %s", len(rows), ticker)
    return len(rows)


# --------------------------------------------------------------------------- #
# Read path — pre-filtered similarity search                                  #
# --------------------------------------------------------------------------- #


async def similarity_search(
    db: AsyncSession,
    *,
    query: str,
    ticker: str,
    top_k: int = DEFAULT_TOP_K,
    filing_type: str | None = None,
    fiscal_year: int | None = None,
    min_year: int | None = None,
    market: str | None = None,
) -> list[RetrievedChunk]:
    """Retrieve the top-K most relevant chunks for ``query``, pre-filtered by metadata.

    ``ticker`` is always applied. The other filters are optional and compose as an
    ``AND`` — this is the SQL pre-filter that hosted vector DBs can only partially
    emulate. Returns chunks ordered by ascending cosine distance (most relevant
    first).
    """
    ticker = ticker.upper()
    query_vec = embeddings.embed_query(query)

    distance = FilingChunk.embedding.cosine_distance(query_vec)

    stmt = select(FilingChunk, distance.label("distance")).where(FilingChunk.ticker == ticker)

    if market is not None:
        stmt = stmt.where(FilingChunk.market == market)
    if filing_type is not None:
        stmt = stmt.where(FilingChunk.filing_type == filing_type)
    if fiscal_year is not None:
        stmt = stmt.where(FilingChunk.fiscal_year == fiscal_year)
    if min_year is not None:
        stmt = stmt.where(FilingChunk.fiscal_year >= min_year)

    stmt = stmt.order_by(distance).limit(top_k)

    result = await db.execute(stmt)
    out: list[RetrievedChunk] = []
    for chunk, dist in result.all():
        out.append(
            RetrievedChunk(
                content=chunk.content,
                ticker=chunk.ticker,
                market=chunk.market,
                filing_type=chunk.filing_type,
                fiscal_year=chunk.fiscal_year,
                section=chunk.section,
                page=chunk.page,
                source_url=chunk.source_url,
                distance=float(dist),
            )
        )
    return out


async def count_chunks(db: AsyncSession, *, ticker: str) -> int:
    """How many filing chunks are indexed for a ticker (0 ⇒ not yet ingested)."""
    stmt = select(func.count()).select_from(FilingChunk).where(
        FilingChunk.ticker == ticker.upper()
    )
    return int(await db.scalar(stmt) or 0)


async def ensure_vector_index(db: AsyncSession) -> None:
    """Create an HNSW index for large corpora (optional).

    Not needed at small scale — per-ticker exact scans are fast and exact. Call
    this once the table grows into the hundreds-of-thousands of chunks. Uses
    cosine ops to match :func:`similarity_search`.
    """
    from sqlalchemy import text

    await db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_filing_chunks_embedding_hnsw "
            "ON filing_chunks USING hnsw (embedding vector_cosine_ops)"
        )
    )
    await db.commit()
