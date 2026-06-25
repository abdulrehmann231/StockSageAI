"""Offline tests for the Phase-4 Filings RAG stack.

No network: the SEC/PSX scrapers are stubbed and embeddings use the deterministic
no-key fallback (so vector search is exercised against real pgvector). Covers the
chunker, embedding fallback, vector store upsert/search, the index pipeline, the
RAG agent (retrieval + extractive fallback + no-data path), and the API contract.
"""

from __future__ import annotations

import pytest

from db.models import Stock
from db.session import SessionLocal
from scrapers.filings_common import FilingDocument, chunk_text, clean_text
from services import embedding_service, filings_index, filings_store


# --------------------------------------------------------------------------- #
# Chunker + embeddings
# --------------------------------------------------------------------------- #


def test_chunk_text_overlaps_and_indexes():
    text = " ".join(f"word{i}" for i in range(2000))
    chunks = chunk_text(text, chunk_words=500, overlap_words=100)
    assert len(chunks) >= 4
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    # consecutive chunks overlap
    first_words = chunks[0].content.split()
    second_words = chunks[1].content.split()
    assert set(first_words[-100:]) & set(second_words[:100])


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_clean_text_collapses_whitespace():
    assert clean_text("a    b\n\n\n\nc  ") == "a b\n\nc"


@pytest.mark.asyncio
async def test_embedding_fallback_is_deterministic_and_normalized(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_2", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    v1 = await embedding_service.embed_query("revenue grew strongly")
    v2 = await embedding_service.embed_query("revenue grew strongly")
    assert v1 == v2
    assert len(v1) == embedding_service.EMBEDDING_DIM
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_embedding_fallback_shared_vocab_closer(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_2", raising=False)
    q = await embedding_service.embed_query("debt and leverage levels")
    near = await embedding_service.embed_query("the company debt leverage is high")
    far = await embedding_service.embed_query("sunny weather beach holiday fun")

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert dot(q, near) > dot(q, far)


# --------------------------------------------------------------------------- #
# Vector store
# --------------------------------------------------------------------------- #


async def _seed_stock(ticker="AAPL", market="GLOBAL"):
    async with SessionLocal() as s:
        s.add(Stock(ticker=ticker, name=f"{ticker} Inc.", market=market, currency="USD"))
        await s.commit()


@pytest.mark.asyncio
async def test_store_upsert_and_search(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_2", raising=False)
    await _seed_stock()

    contents = [
        "Total revenue increased 12% year over year driven by services.",
        "Risk factors include supply chain concentration and FX exposure.",
        "The board declared a quarterly dividend of forty cents per share.",
    ]
    embeddings = await embedding_service.embed_texts(contents)

    async with SessionLocal() as db:
        filing = await filings_store.upsert_filing(
            db, ticker="AAPL", market="GLOBAL", source="sec_edgar",
            external_id="acc-1", filing_type="10-K", fiscal_year=2025,
        )
        chunks = [
            filings_store.ChunkInput(content=c, chunk_index=i)
            for i, c in enumerate(contents)
        ]
        n = await filings_store.replace_chunks(db, filing, chunks, embeddings)
        await db.commit()
        assert n == 3

        qvec = await embedding_service.embed_query("how did revenue change this year?")
        results = await filings_store.search(db, ticker="AAPL", query_embedding=qvec, k=2)
        assert len(results) == 2
        # the revenue chunk should rank first
        assert "revenue" in results[0].content.lower()
        assert results[0].similarity >= results[1].similarity


@pytest.mark.asyncio
async def test_store_reindex_replaces_not_duplicates(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    await _seed_stock("MSFT")
    async with SessionLocal() as db:
        filing = await filings_store.upsert_filing(
            db, ticker="MSFT", market="GLOBAL", source="sec_edgar", external_id="m1",
        )
        emb = await embedding_service.embed_texts(["first version of the filing text"])
        await filings_store.replace_chunks(
            db, filing, [filings_store.ChunkInput(content="first version", chunk_index=0)], emb
        )
        await db.commit()

        # Re-index same filing identity → chunks replaced.
        filing2 = await filings_store.upsert_filing(
            db, ticker="MSFT", market="GLOBAL", source="sec_edgar", external_id="m1",
        )
        emb2 = await embedding_service.embed_texts(["a", "b"])
        await filings_store.replace_chunks(
            db, filing2,
            [filings_store.ChunkInput(content="a", chunk_index=0),
             filings_store.ChunkInput(content="b", chunk_index=1)],
            emb2,
        )
        await db.commit()
        status = await filings_store.filing_status(db, "MSFT")
        assert status["filing_count"] == 1
        assert status["chunk_count"] == 2


# --------------------------------------------------------------------------- #
# Index pipeline (stubbed scrapers)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_index_ticker_pipeline(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    await _seed_stock("AAPL")

    long_text = " ".join(f"token{i}" for i in range(1600))

    async def fake_sec(ticker, **kwargs):
        return [
            FilingDocument(
                ticker="AAPL", market="GLOBAL", source="sec_edgar",
                external_id="acc-x", text=long_text, filing_type="10-K", fiscal_year=2025,
            )
        ]

    monkeypatch.setattr(filings_index.sec_edgar, "fetch_latest_filings", fake_sec)
    async with SessionLocal() as db:
        summary = await filings_index.index_ticker(db, ticker="AAPL", market="GLOBAL")
    assert summary["indexed_filings"] == 1
    assert summary["indexed_chunks"] >= 2
    assert summary["errors"] == []


@pytest.mark.asyncio
async def test_index_ticker_handles_no_documents(monkeypatch):
    await _seed_stock("AAPL")

    async def empty(ticker, **kwargs):
        return []

    monkeypatch.setattr(filings_index.sec_edgar, "fetch_latest_filings", empty)
    async with SessionLocal() as db:
        summary = await filings_index.index_ticker(db, ticker="AAPL", market="GLOBAL")
    assert summary["indexed_filings"] == 0
    assert summary["errors"]


@pytest.mark.asyncio
async def test_index_routes_psx_to_psx_scraper(monkeypatch):
    await _seed_stock("ENGRO", market="PSX")
    called = {}

    async def fake_psx(ticker, **kwargs):
        called["psx"] = ticker
        return []

    async def fail_sec(ticker, **kwargs):
        called["sec"] = ticker
        return []

    monkeypatch.setattr(filings_index.psx_filings, "fetch_latest_filings", fake_psx)
    monkeypatch.setattr(filings_index.sec_edgar, "fetch_latest_filings", fail_sec)
    async with SessionLocal() as db:
        await filings_index.index_ticker(db, ticker="ENGRO", market="PSX")
    assert called == {"psx": "ENGRO"}


# --------------------------------------------------------------------------- #
# RAG agent
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rag_no_index_returns_not_grounded(monkeypatch, client):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from agents import filings_agent

    await _seed_stock("AAPL")
    async with SessionLocal() as db:
        ans = await filings_agent.answer_question(
            db, ticker="AAPL", market="GLOBAL", question="What is the revenue trend?"
        )
    assert ans.grounded is False
    assert ans.citations == []


@pytest.mark.asyncio
async def test_rag_extractive_fallback_when_no_llm(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from agents import filings_agent

    await _seed_stock("AAPL")
    contents = ["Total revenue rose 12% year over year to a record high."]
    emb = await embedding_service.embed_texts(contents)
    async with SessionLocal() as db:
        filing = await filings_store.upsert_filing(
            db, ticker="AAPL", market="GLOBAL", source="sec_edgar",
            external_id="acc-1", filing_type="10-K", fiscal_year=2025,
        )
        await filings_store.replace_chunks(
            db, filing,
            [filings_store.ChunkInput(content=contents[0], chunk_index=0)], emb,
        )
        await db.commit()

        ans = await filings_agent.answer_question(
            db, ticker="AAPL", market="GLOBAL", question="How did revenue change?"
        )
    assert ans.grounded is True
    assert ans.model_used is None  # extractive fallback
    assert "revenue" in ans.answer.lower()
    assert ans.citations[0].citation == "10-K FY2025"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


async def _signup(client, email="filings@example.com"):
    res = await client.post(
        "/api/auth/signup", json={"email": email, "password": "supersecret"}
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_filings_api_requires_auth(client):
    assert (await client.get("/api/filings/AAPL/status")).status_code == 401
    assert (
        await client.post("/api/filings/AAPL/ask", json={"question": "revenue?"})
    ).status_code == 401


@pytest.mark.asyncio
async def test_filings_api_index_and_ask(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _signup(client)
    await _seed_stock("AAPL")

    long_text = "Revenue grew 12% year over year. " + " ".join(
        f"detail{i}" for i in range(1200)
    )

    async def fake_sec(ticker, **kwargs):
        return [
            FilingDocument(
                ticker="AAPL", market="GLOBAL", source="sec_edgar",
                external_id="acc-1", text=long_text, filing_type="10-K", fiscal_year=2025,
            )
        ]

    monkeypatch.setattr(filings_index.sec_edgar, "fetch_latest_filings", fake_sec)

    idx = await client.post("/api/filings/AAPL/index", json={"limit": 1})
    assert idx.status_code == 200, idx.text
    assert idx.json()["indexed_chunks"] >= 1

    st = await client.get("/api/filings/AAPL/status")
    assert st.json()["chunk_count"] >= 1

    ask = await client.post(
        "/api/filings/AAPL/ask", json={"question": "What is the revenue trend?", "k": 3}
    )
    assert ask.status_code == 200, ask.text
    body = ask.json()
    assert body["grounded"] is True
    assert len(body["citations"]) >= 1


@pytest.mark.asyncio
async def test_filings_api_unknown_ticker_404(client):
    await _signup(client)
    assert (await client.get("/api/filings/NOPE/status")).status_code == 404
