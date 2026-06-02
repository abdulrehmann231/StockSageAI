"""Filings RAG Agent (plan § 4.6).

Runtime flow (read-only — ingestion lives in ``ingestion/pipeline.py``):

1. Receive a ticker (+ market, company).
2. Auto-generate the standard key questions: revenue trend, profit margin, debt
   level, risks, outlook.
3. For each question: embed it, run a **pre-filtered** pgvector similarity search
   scoped to the ticker, take the top-K chunks, and ask the LLM for a grounded,
   cited answer.
4. When the LLM is unavailable, fall back to a deterministic extractive answer
   built from the retrieved chunks, so the agent always returns something useful.
5. Compile into a structured ``FilingsResult`` and cache it (6h Redis TTL).

LangGraph-compatible ``filings_agent(state)`` node wrapper is provided for the
Phase 5 orchestrator. A small CLI tester is included for local runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from db.schemas import FilingAnswer, FilingCitation, FilingsResult
from db.session import SessionLocal
from services import cache_service, llm_service, vector_store

logger = logging.getLogger(__name__)

CACHE_PREFIX = "filings:"

TOP_K = 5
SNIPPET_CHARS = 280


@dataclass(slots=True)
class KeyQuestion:
    key: str
    question: str


# The five standard questions from plan § 4.6.
KEY_QUESTIONS: list[KeyQuestion] = [
    KeyQuestion("revenue_trend", "What is the company's recent revenue trend and growth?"),
    KeyQuestion("profit_margin", "What are the company's profit margins and profitability trend?"),
    KeyQuestion("debt_level", "What is the company's debt level and leverage situation?"),
    KeyQuestion("risks", "What are the most significant risk factors disclosed?"),
    KeyQuestion("outlook", "What is management's forward-looking guidance or outlook?"),
]


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


async def get_filings_analysis(
    ticker: str,
    market: str,
    *,
    company_name: str | None = None,
    db: AsyncSession | None = None,
    use_cache: bool = True,
) -> FilingsResult:
    """Run the Filings RAG flow for a ticker and return structured ``filings_data``."""
    ticker = ticker.upper()
    cache_key = f"{CACHE_PREFIX}{market}:{ticker}"

    if use_cache:
        cached = await _get_cached(cache_key)
        if cached is not None:
            return cached

    owns_session = db is None
    db = db or SessionLocal()
    try:
        indexed = await vector_store.count_chunks(db, ticker=ticker)
        answers: list[FilingAnswer] = []

        if indexed == 0:
            # Nothing ingested yet — return an explicit, non-cached empty result so
            # a later ingestion is picked up on the next call.
            result = FilingsResult(
                ticker=ticker,
                market=market,
                company_name=company_name,
                answers=[
                    FilingAnswer(
                        question=q.question,
                        answer=(
                            "No filings have been indexed for this ticker yet. "
                            "Run the ingestion pipeline to enable filings Q&A."
                        ),
                        grounded=False,
                    )
                    for q in KEY_QUESTIONS
                ],
                chunks_indexed=0,
                fetched_at=datetime.now(timezone.utc),
                cached=False,
            )
            return result

        for q in KEY_QUESTIONS:
            answers.append(await _answer_question(db, ticker, q))

        result = FilingsResult(
            ticker=ticker,
            market=market,
            company_name=company_name,
            answers=answers,
            chunks_indexed=indexed,
            fetched_at=datetime.now(timezone.utc),
            cached=False,
        )
    finally:
        if owns_session:
            await db.close()

    await _set_cached(cache_key, result)
    return result


async def _answer_question(db: AsyncSession, ticker: str, q: KeyQuestion) -> FilingAnswer:
    chunks = await vector_store.similarity_search(
        db, query=q.question, ticker=ticker, top_k=TOP_K
    )
    if not chunks:
        return FilingAnswer(
            question=q.question,
            answer="The indexed filings do not contain enough information to answer this.",
            grounded=False,
        )

    citations = [
        FilingCitation(
            filing_type=c.filing_type,
            fiscal_year=c.fiscal_year,
            section=c.section,
            page=c.page,
            source_url=c.source_url,
            snippet=_snippet(c.content),
            distance=round(c.distance, 4),
        )
        for c in chunks
    ]

    context = [
        {
            "content": c.content,
            "filing_type": c.filing_type,
            "fiscal_year": c.fiscal_year,
            "section": c.section,
            "page": c.page,
        }
        for c in chunks
    ]

    llm_answer = await llm_service.answer_from_filings(
        ticker=ticker, question=q.question, context_chunks=context
    )
    answer_text = llm_answer or _extractive_fallback(chunks)

    return FilingAnswer(
        question=q.question,
        answer=answer_text,
        citations=citations,
        grounded=True,
    )


def _extractive_fallback(chunks) -> str:
    """Deterministic answer when the LLM is unavailable: stitch top snippets.

    Honest about being an excerpt rather than a synthesized answer.
    """
    top = chunks[0]
    cite = f"{top.filing_type}"
    if top.fiscal_year:
        cite += f" FY{top.fiscal_year}"
    if top.page:
        cite += f", p.{top.page}"
    return f"From the filings ({cite}): {_snippet(top.content, limit=500)}"


def _snippet(text: str, *, limit: int = SNIPPET_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


# --------------------------------------------------------------------------- #
# Caching                                                                     #
# --------------------------------------------------------------------------- #


async def _get_cached(cache_key: str) -> FilingsResult | None:
    try:
        raw = await cache_service.get_json(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.info("Filings cache read failed (%s); fetching fresh.", exc)
        return None
    if not raw:
        return None
    raw["cached"] = True
    return FilingsResult.model_validate(raw)


async def _set_cached(cache_key: str, result: FilingsResult) -> None:
    from core.config import get_settings

    ttl = get_settings().filings_cache_ttl_seconds
    try:
        await cache_service.set_json(cache_key, result.model_dump(mode="json"), ttl_seconds=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.info("Filings cache write failed (%s); continuing.", exc)


# --------------------------------------------------------------------------- #
# LangGraph node wrapper                                                       #
# --------------------------------------------------------------------------- #


async def filings_agent(state: dict) -> dict:
    """LangGraph node: read ``ticker``/``market`` from state, write ``filings_data``."""
    ticker = state["ticker"]
    market = state.get("market", "GLOBAL")
    company_name = state.get("company_name")
    result = await get_filings_analysis(ticker, market, company_name=company_name)
    return {**state, "filings_data": result.model_dump(mode="json")}


# --------------------------------------------------------------------------- #
# CLI tester                                                                   #
# --------------------------------------------------------------------------- #


async def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the Filings RAG agent for a ticker")
    parser.add_argument("ticker")
    parser.add_argument("market", nargs="?", default="GLOBAL", choices=["GLOBAL", "PSX"])
    parser.add_argument("--company", default=None)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    result = await get_filings_analysis(
        args.ticker, args.market, company_name=args.company, use_cache=not args.no_cache
    )
    print(f"\n=== Filings analysis: {result.ticker} ({result.market}) ===")
    print(f"chunks indexed: {result.chunks_indexed}\n")
    for ans in result.answers:
        print(f"Q: {ans.question}")
        print(f"A: {ans.answer}")
        if ans.citations:
            print(f"   ({len(ans.citations)} citations)")
        print()


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_cli())
