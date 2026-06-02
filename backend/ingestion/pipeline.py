"""Filings ingestion orchestration: fetch → chunk → embed → upsert.

Ties the pieces together for both markets:
- Global (US): SEC EDGAR (``sec_edgar``) → primary doc text.
- PSX: annual-report PDFs (TODO: scraper + ``pypdf``) → text.

Run as a CLI for a one-time / periodic index build::

    python -m ingestion.pipeline AAPL GLOBAL
    python -m ingestion.pipeline ENGRO PSX --pdf path/to/report.pdf

The runtime agent never calls this — it only reads the index that this builds.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from db.session import SessionLocal
from ingestion import sec_edgar
from ingestion.chunking import chunk_text
from services import vector_store

logger = logging.getLogger(__name__)


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
        for tc in chunk_text(text):
            all_chunks.append(
                {
                    "content": tc.content,
                    "chunk_index": tc.chunk_index,
                    "filing_type": ref.form,
                    "fiscal_year": ref.fiscal_year,
                    "source_url": ref.document_url,
                }
            )

    if not all_chunks:
        logger.info("No extractable text for %s; nothing ingested.", ticker)
        return 0

    return await vector_store.upsert_chunks(
        db, ticker=ticker, market="GLOBAL", chunks=all_chunks
    )


async def ingest_psx_pdf(
    db: AsyncSession,
    ticker: str,
    *,
    pdf_path: str,
    fiscal_year: int | None = None,
) -> int:
    """Ingest a PSX annual-report PDF from disk into the vector store.

    Uses ``pypdf`` for text extraction. PSX reports are often poorly formatted;
    LLM cleanup of messy pages is a documented future enhancement (plan § 4.6).
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("pypdf is required to ingest PSX PDFs") from exc

    reader = PdfReader(pdf_path)
    chunks: list[dict] = []
    for page_no, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        for tc in chunk_text(page_text):
            chunks.append(
                {
                    "content": tc.content,
                    "chunk_index": len(chunks),
                    "filing_type": "annual_report",
                    "fiscal_year": fiscal_year,
                    "page": page_no,
                    "source_url": pdf_path,
                }
            )

    if not chunks:
        return 0
    return await vector_store.upsert_chunks(db, ticker=ticker, market="PSX", chunks=chunks)


async def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest filings into the pgvector store")
    parser.add_argument("ticker")
    parser.add_argument("market", choices=["GLOBAL", "PSX"])
    parser.add_argument("--pdf", help="Path to a PSX annual-report PDF (PSX market only)")
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args()

    async with SessionLocal() as db:
        if args.market == "GLOBAL":
            n = await ingest_global_ticker(db, args.ticker)
        else:
            if not args.pdf:
                parser.error("PSX ingestion requires --pdf")
            n = await ingest_psx_pdf(db, args.ticker, pdf_path=args.pdf, fiscal_year=args.year)
    print(f"Ingested {n} chunks for {args.ticker} ({args.market}).")


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
