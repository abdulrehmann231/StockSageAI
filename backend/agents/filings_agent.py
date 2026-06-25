"""Filings RAG Agent (Phase 4, plan § 4.6) — "most technically impressive".

Runtime flow:

1. Receive a ticker (+ market) and a question — or auto-generate the plan's five
   key questions (revenue trend, profit margin, debt level, risks, outlook).
2. Embed the question, vector-search the ticker's indexed filing chunks
   (pgvector cosine), take the top-k.
3. Hand the chunks to the LLM for a grounded answer **with citations**
   (filing type + fiscal year). When no LLM key is configured, fall back to an
   extractive answer that quotes the most relevant chunk — so the feature stays
   useful offline and in tests.

The agent never invents data: if nothing is indexed for the ticker it says so,
and the LLM is instructed to answer only from the provided excerpts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from db.schemas import FilingCitation, FilingsAnswer, FilingsData
from services import embedding_service, filings_store, llm_service

logger = logging.getLogger(__name__)

# Plan § 4.6: auto-generated key questions.
KEY_QUESTIONS: dict[str, str] = {
    "revenue_trend": "What is the revenue trend and how did total revenue change year over year?",
    "profit_margin": "What are the profit margins and how has profitability changed?",
    "debt_level": "What is the company's debt level and leverage situation?",
    "risks": "What are the most significant risk factors disclosed?",
    "outlook": "What guidance or outlook does management provide for the future?",
}


def _citation_tag(chunk: filings_store.RetrievedChunk) -> str:
    parts = [chunk.filing_type or "filing"]
    if chunk.fiscal_year:
        parts.append(f"FY{chunk.fiscal_year}")
    return " ".join(parts)


async def answer_question(
    db: AsyncSession,
    *,
    ticker: str,
    market: str,
    question: str,
    k: int = 5,
) -> FilingsAnswer:
    """Answer a single question grounded in the ticker's indexed filings."""
    ticker = ticker.strip().upper()
    query_vec = await embedding_service.embed_query(question)
    chunks = await filings_store.search(db, ticker=ticker, query_embedding=query_vec, k=k)

    if not chunks:
        return FilingsAnswer(
            ticker=ticker,
            question=question,
            answer=(
                "No filings are indexed for this ticker yet. Run indexing first "
                "(POST /api/filings/{ticker}/index)."
            ),
            citations=[],
            grounded=False,
            model_used=None,
            fetched_at=datetime.now(timezone.utc),
        )

    citations = [
        FilingCitation(
            citation=_citation_tag(c),
            filing_type=c.filing_type,
            fiscal_year=c.fiscal_year,
            section=c.section,
            page=c.page,
            url=c.url,
            similarity=c.similarity,
            excerpt=c.content[:400],
        )
        for c in chunks
    ]

    llm_answer = await llm_service.answer_from_filings(
        ticker=ticker,
        question=question,
        chunks=[{"content": c.content, "citation": _citation_tag(c)} for c in chunks],
    )

    if llm_answer:
        answer, model_used = llm_answer, "llm"
    else:
        answer, model_used = _extractive_answer(chunks), None

    return FilingsAnswer(
        ticker=ticker,
        question=question,
        answer=answer,
        citations=citations,
        grounded=True,
        model_used=model_used,
        fetched_at=datetime.now(timezone.utc),
    )


def _extractive_answer(chunks: list[filings_store.RetrievedChunk]) -> str:
    """Deterministic fallback: quote the single most relevant excerpt."""
    top = chunks[0]
    snippet = " ".join(top.content.split()[:80])
    return f"From the {_citation_tag(top)}: \"{snippet}…\""


async def auto_analysis(
    db: AsyncSession,
    *,
    ticker: str,
    market: str,
    k: int = 5,
) -> FilingsData:
    """Answer the five key questions and compile a structured ``FilingsData``."""
    ticker = ticker.strip().upper()
    status = await filings_store.filing_status(db, ticker)
    answers: dict[str, FilingsAnswer] = {}
    for key, question in KEY_QUESTIONS.items():
        answers[key] = await answer_question(
            db, ticker=ticker, market=market, question=question, k=k
        )

    indexed = status["chunk_count"] > 0
    return FilingsData(
        ticker=ticker,
        market=market.strip().upper(),
        indexed=indexed,
        filing_count=status["filing_count"],
        chunk_count=status["chunk_count"],
        answers=answers,
        fetched_at=datetime.now(timezone.utc),
    )
