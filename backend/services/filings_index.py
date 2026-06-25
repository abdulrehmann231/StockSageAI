"""Filings indexing pipeline (Phase 4, plan § 4.6).

Ties the pieces together: **fetch** source documents (SEC EDGAR for GLOBAL, PSX
for PSX) → **chunk** → **embed** (Gemini, with offline fallback) → **upsert**
into pgvector. Idempotent: re-indexing a ticker replaces that filing's chunks
rather than duplicating them.

``index_ticker`` is callable directly from the API or a Celery refresh job. It
returns a small summary dict (per-filing chunk counts + any errors) so the
endpoint can report what happened without leaking internals.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from scrapers import psx_filings, sec_edgar
from scrapers.filings_common import FilingDocument, chunk_text
from services import embedding_service, filings_store

logger = logging.getLogger(__name__)


async def _fetch_documents(ticker: str, market: str, *, limit: int) -> list[FilingDocument]:
    """Route to the right source scraper for the market."""
    if market.strip().upper() == "PSX":
        return await psx_filings.fetch_latest_filings(ticker, limit=limit)
    return await sec_edgar.fetch_latest_filings(ticker, limit=limit)


async def index_ticker(
    db: AsyncSession,
    *,
    ticker: str,
    market: str,
    limit: int = 1,
) -> dict:
    """Fetch, chunk, embed, and store filings for one ticker."""
    ticker = ticker.strip().upper()
    market = (market or "GLOBAL").strip().upper()
    summary: dict = {
        "ticker": ticker,
        "market": market,
        "indexed_filings": 0,
        "indexed_chunks": 0,
        "filings": [],
        "errors": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        documents = await _fetch_documents(ticker, market, limit=limit)
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"fetch: {exc}")
        return summary

    if not documents:
        summary["errors"].append("No source filings found or source unavailable.")
        return summary

    for doc in documents:
        try:
            chunks = chunk_text(doc.text, section=doc.filing_type)
            if not chunks:
                summary["errors"].append(f"{doc.external_id}: no chunks produced")
                continue

            embeddings = await embedding_service.embed_texts([c.content for c in chunks])

            filing = await filings_store.upsert_filing(
                db,
                ticker=ticker,
                market=doc.market,
                source=doc.source,
                external_id=doc.external_id,
                filing_type=doc.filing_type,
                fiscal_year=doc.fiscal_year,
                title=doc.title,
                url=doc.url,
            )
            chunk_inputs = [
                filings_store.ChunkInput(
                    content=c.content, chunk_index=c.chunk_index, section=c.section, page=c.page
                )
                for c in chunks
            ]
            n = await filings_store.replace_chunks(db, filing, chunk_inputs, embeddings)
            summary["indexed_filings"] += 1
            summary["indexed_chunks"] += n
            summary["filings"].append(
                {
                    "external_id": doc.external_id,
                    "filing_type": doc.filing_type,
                    "fiscal_year": doc.fiscal_year,
                    "chunks": n,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("Indexing failed for %s/%s: %s", ticker, doc.external_id, exc)
            summary["errors"].append(f"{doc.external_id}: {exc}")

    await db.commit()
    return summary
