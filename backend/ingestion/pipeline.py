"""Filings ingestion orchestration: fetch → chunk → embed → upsert.

Ties the pieces together for both markets:
- Global (US): SEC EDGAR (``ingestion.sec_edgar``) → primary doc text, segmented
  into Item sections → chunks tagged with ``section``.
- PSX: annual-report PDFs (``scrapers.psx_filings``) → per-page text → chunks
  tagged with ``page``. Supports both auto-discovery and a local PDF file.

Run as a CLI for a one-time / periodic index build::

    python -m ingestion.pipeline AAPL GLOBAL
    python -m ingestion.pipeline ENGRO PSX                 # auto-discover PDFs
    python -m ingestion.pipeline ENGRO PSX --pdf report.pdf --year 2023

The runtime agent never calls this — it only reads the index that this builds.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from db.session import SessionLocal
from ingestion import sec_edgar
from ingestion.chunking import chunk_text
from scrapers import psx_filings
from services import vector_store

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Global (SEC EDGAR)                                                          #
# --------------------------------------------------------------------------- #


async def ingest_global_ticker(
    db: AsyncSession,
    ticker: str,
    *,
    max_filings: int = 2,
) -> int:
    """Ingest a US ticker's recent SEC filings into the vector store."""
    refs = await sec_edgar.list_recent_filings(ticker, limit=max_filings)
    if not refs:
        logger.info("No SEC filings found for %s (or fetch failed).", ticker)
        return 0

    all_chunks: list[dict] = []
    for ref in refs:
        text = await sec_edgar.fetch_filing_text(ref)
        if not text.strip():
            continue
        # Split into Item sections so each chunk carries a meaningful section label.
        for section, segment in sec_edgar.segment_sec_text(text):
            for tc in chunk_text(segment):
                all_chunks.append(
                    {
                        "content": tc.content,
                        "chunk_index": len(all_chunks),
                        "filing_type": ref.form,
                        "fiscal_year": ref.fiscal_year,
                        "section": section,
                        "source_url": ref.document_url,
                    }
                )

    if not all_chunks:
        logger.info("No extractable text for %s; nothing ingested.", ticker)
        return 0

    return await vector_store.upsert_chunks(
        db, ticker=ticker, market="GLOBAL", chunks=all_chunks
    )


# --------------------------------------------------------------------------- #
# PSX (annual-report PDFs)                                                    #
# --------------------------------------------------------------------------- #


async def ingest_psx_ticker(
    db: AsyncSession,
    ticker: str,
    *,
    max_reports: int = 2,
) -> int:
    """Auto-discover, download, and ingest a PSX ticker's annual-report PDFs."""
    pages = await psx_filings.fetch_psx_filing_pages(ticker, max_reports=max_reports)
    if not pages:
        logger.info("No PSX report pages found for %s (or discovery failed).", ticker)
        return 0
    return await _ingest_psx_pages(db, ticker, pages)


async def ingest_psx_pdf(
    db: AsyncSession,
    ticker: str,
    *,
    pdf_path: str,
    fiscal_year: int | None = None,
) -> int:
    """Ingest a PSX annual-report PDF already downloaded to disk."""
    pages = psx_filings.extract_pdf_pages(
        pdf_path, source_url=pdf_path, fiscal_year=fiscal_year
    )
    if not pages:
        logger.info("No extractable text in %s; nothing ingested.", pdf_path)
        return 0
    return await _ingest_psx_pages(db, ticker, pages)


async def _ingest_psx_pages(
    db: AsyncSession, ticker: str, pages: list[psx_filings.FilingPage]
) -> int:
    chunks: list[dict] = []
    for fp in pages:
        for tc in chunk_text(fp.text):
            chunks.append(
                {
                    "content": tc.content,
                    "chunk_index": len(chunks),
                    "filing_type": "annual_report",
                    "fiscal_year": fp.fiscal_year,
                    "page": fp.page,
                    "source_url": fp.source_url,
                }
            )
    if not chunks:
        return 0
    return await vector_store.upsert_chunks(db, ticker=ticker, market="PSX", chunks=chunks)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


async def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest filings into the pgvector store")
    parser.add_argument("ticker")
    parser.add_argument("market", choices=["GLOBAL", "PSX"])
    parser.add_argument("--pdf", help="Path to a local PSX annual-report PDF (PSX only)")
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args()

    async with SessionLocal() as db:
        if args.market == "GLOBAL":
            n = await ingest_global_ticker(db, args.ticker)
        elif args.pdf:
            n = await ingest_psx_pdf(db, args.ticker, pdf_path=args.pdf, fiscal_year=args.year)
        else:
            n = await ingest_psx_ticker(db, args.ticker)
    print(f"Ingested {n} chunks for {args.ticker} ({args.market}).")


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
