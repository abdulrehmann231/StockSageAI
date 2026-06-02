"""Tests for the Filings RAG agent runtime.

The vector store and LLM are stubbed, so these run without a DB or network and
assert the agent's orchestration: question generation, citation assembly, the
LLM→extractive fallback, and the empty-index path.
"""

from __future__ import annotations

import pytest

from agents import filings_agent
from agents.filings_agent import KEY_QUESTIONS
from services import vector_store


class _Sentinel:
    """Stand-in DB handle; never actually used because the store is stubbed."""


def _chunk(content="Revenue rose 18% to $94.9B in fiscal 2023.", **kw):
    defaults = dict(
        content=content,
        ticker="AAPL",
        market="GLOBAL",
        filing_type="10-K",
        fiscal_year=2023,
        section="MD&A",
        page=42,
        source_url="https://sec.gov/x",
        distance=0.12,
    )
    defaults.update(kw)
    return vector_store.RetrievedChunk(**defaults)


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    # Make caching a no-op so tests never touch Redis.
    async def _get(_key):
        return None

    async def _set(_key, _result):
        return None

    monkeypatch.setattr(filings_agent, "_get_cached", _get)
    monkeypatch.setattr(filings_agent, "_set_cached", _set)


async def test_empty_index_returns_ungrounded_stub(monkeypatch):
    async def _count(_db, *, ticker):
        return 0

    monkeypatch.setattr(vector_store, "count_chunks", _count)

    result = await filings_agent.get_filings_analysis(
        "AAPL", "GLOBAL", db=_Sentinel(), use_cache=False
    )
    assert result.chunks_indexed == 0
    assert len(result.answers) == len(KEY_QUESTIONS)
    assert all(not a.grounded for a in result.answers)


async def test_grounded_answers_use_llm(monkeypatch):
    async def _count(_db, *, ticker):
        return 25

    async def _search(_db, *, query, ticker, top_k=5, **kw):
        return [_chunk(), _chunk(content="Operating margin expanded to 30%.", page=44)]

    async def _llm(*, ticker, question, context_chunks):
        return "Revenue grew 18% (10-K FY2023, p.42)."

    monkeypatch.setattr(vector_store, "count_chunks", _count)
    monkeypatch.setattr(vector_store, "similarity_search", _search)
    monkeypatch.setattr(filings_agent.llm_service, "answer_from_filings", _llm)

    result = await filings_agent.get_filings_analysis(
        "AAPL", "GLOBAL", db=_Sentinel(), use_cache=False
    )
    assert result.chunks_indexed == 25
    assert len(result.answers) == len(KEY_QUESTIONS)
    first = result.answers[0]
    assert first.grounded
    assert "18%" in first.answer
    assert len(first.citations) == 2
    assert first.citations[0].filing_type == "10-K"
    assert first.citations[0].page == 42


async def test_extractive_fallback_when_llm_unavailable(monkeypatch):
    async def _count(_db, *, ticker):
        return 5

    async def _search(_db, *, query, ticker, top_k=5, **kw):
        return [_chunk(content="Total debt decreased to $1.2B from $1.8B.")]

    async def _llm(*, ticker, question, context_chunks):
        return None  # simulate no API key / failure

    monkeypatch.setattr(vector_store, "count_chunks", _count)
    monkeypatch.setattr(vector_store, "similarity_search", _search)
    monkeypatch.setattr(filings_agent.llm_service, "answer_from_filings", _llm)

    result = await filings_agent.get_filings_analysis(
        "AAPL", "GLOBAL", db=_Sentinel(), use_cache=False
    )
    ans = result.answers[0]
    assert ans.grounded
    assert "From the filings" in ans.answer
    assert "1.2B" in ans.answer


async def test_question_with_no_chunks_is_ungrounded(monkeypatch):
    async def _count(_db, *, ticker):
        return 5

    async def _search(_db, *, query, ticker, top_k=5, **kw):
        return []  # nothing relevant for any question

    monkeypatch.setattr(vector_store, "count_chunks", _count)
    monkeypatch.setattr(vector_store, "similarity_search", _search)

    result = await filings_agent.get_filings_analysis(
        "AAPL", "GLOBAL", db=_Sentinel(), use_cache=False
    )
    assert all(not a.grounded for a in result.answers)
    assert all(not a.citations for a in result.answers)


async def test_node_wrapper_populates_filings_data(monkeypatch):
    async def _count(_db, *, ticker):
        return 0

    monkeypatch.setattr(vector_store, "count_chunks", _count)

    out = await filings_agent.filings_agent(
        {"ticker": "AAPL", "market": "GLOBAL", "company_name": "Apple Inc."}
    )
    assert "filings_data" in out
    assert out["filings_data"]["ticker"] == "AAPL"
    assert out["ticker"] == "AAPL"  # original state preserved
