"""Tests for the Phase-6 Chat-with-stock endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents import orchestrator
from agents.report_writer import StockReport
from db.models import Stock
from db.schemas import PriceQuote
from db.session import SessionLocal


async def _signup(client, email="chatter@example.com"):
    res = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "supersecret"},
    )
    assert res.status_code == 201


async def _seed_stock():
    async with SessionLocal() as session:
        session.add(Stock(ticker="AAPL", name="Apple Inc.", market="GLOBAL", currency="USD"))
        await session.commit()


def _report() -> StockReport:
    price = PriceQuote(
        ticker="AAPL",
        market="GLOBAL",
        currency="USD",
        price=150.0,
        pe_ratio=28.5,
        eps=5.0,
        dividend_yield=0.5,
        fetched_at=datetime.now(timezone.utc),
        source="yfinance",
    )
    return StockReport(
        ticker="AAPL",
        market="GLOBAL",
        company_name="Apple Inc.",
        verdict="BUY",
        confidence="medium",
        composite_score=0.4,
        executive_summary="Apple looks bullish into earnings on strong services growth.",
        price=price,
        sources=["yfinance"],
        fetched_at=datetime.now(timezone.utc),
    )


def _patch_orchestrator(monkeypatch):
    async def _stub(ticker, market, **kwargs):
        return _report()

    monkeypatch.setattr(orchestrator, "get_report", _stub)


async def _create_report(client):
    res = await client.post("/api/reports/generate", json={"ticker": "AAPL"})
    assert res.status_code == 201, res.text
    return res.json()


@pytest.mark.asyncio
async def test_chat_history_requires_auth(client):
    import uuid

    res = await client.get(f"/api/chat/{uuid.uuid4()}/history")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_chat_history_unknown_report_404(client, monkeypatch):
    import uuid

    await _signup(client)
    res = await client.get(f"/api/chat/{uuid.uuid4()}/history")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_chat_deterministic_pe_lookup_when_llm_offline(client, monkeypatch):
    """No OPENROUTER_API_KEY → deterministic reply for a P/E question."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)
    report = await _create_report(client)

    res = await client.post(
        f"/api/chat/{report['id']}/message",
        json={"content": "What's the P/E?"},
    )
    assert res.status_code == 201, res.text
    turn = res.json()
    assert turn["user_message"]["content"] == "What's the P/E?"
    # Deterministic formatter → "P/E: 28.50."
    assert "P/E" in turn["assistant_message"]["content"]
    assert "28.5" in turn["assistant_message"]["content"]


@pytest.mark.asyncio
async def test_chat_deterministic_missing_field_message(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)
    report = await _create_report(client)

    res = await client.post(
        f"/api/chat/{report['id']}/message",
        json={"content": "What's the market cap?"},
    )
    body = res.json()["assistant_message"]["content"]
    assert "Market cap" in body
    assert "not in the current report" in body


@pytest.mark.asyncio
async def test_chat_uses_llm_when_configured(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)
    report = await _create_report(client)

    captured = {}

    async def _stub(*, question, report_payload, history):
        captured["question"] = question
        captured["report_payload"] = report_payload
        captured["history"] = history
        return "Apple's services growth outpaced devices last quarter."

    from services import llm_service

    monkeypatch.setattr(llm_service, "answer_chat_question", _stub)

    res = await client.post(
        f"/api/chat/{report['id']}/message",
        json={"content": "Why bullish?"},
    )
    assert res.status_code == 201
    assert (
        res.json()["assistant_message"]["content"]
        == "Apple's services growth outpaced devices last quarter."
    )
    assert captured["question"] == "Why bullish?"
    assert captured["report_payload"]["ticker"] == "AAPL"
    assert captured["history"] == []  # first turn


@pytest.mark.asyncio
async def test_chat_persists_history_and_passes_prior_turns(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)
    report = await _create_report(client)

    call_log: list[list[dict]] = []

    async def _stub(*, question, report_payload, history):
        call_log.append(list(history))
        return f"echo: {question}"

    from services import llm_service

    monkeypatch.setattr(llm_service, "answer_chat_question", _stub)

    await client.post(f"/api/chat/{report['id']}/message", json={"content": "Q1?"})
    await client.post(f"/api/chat/{report['id']}/message", json={"content": "Q2?"})

    assert call_log[0] == []  # first call, no prior history
    # Second call sees the first user+assistant turn already in history.
    assert call_log[1] == [
        {"role": "user", "content": "Q1?"},
        {"role": "assistant", "content": "echo: Q1?"},
    ]

    history = (await client.get(f"/api/chat/{report['id']}/history")).json()
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "Q1?"),
        ("assistant", "echo: Q1?"),
        ("user", "Q2?"),
        ("assistant", "echo: Q2?"),
    ]


@pytest.mark.asyncio
async def test_chat_is_user_scoped(client, monkeypatch):
    """Bob can't chat against Alice's report."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)

    await _signup(client, email="alice@example.com")
    report = await _create_report(client)

    await client.post("/api/auth/logout")
    await _signup(client, email="bob@example.com")
    res = await client.post(
        f"/api/chat/{report['id']}/message",
        json={"content": "What's the P/E?"},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_chat_empty_message_rejected(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)
    report = await _create_report(client)

    res = await client.post(
        f"/api/chat/{report['id']}/message", json={"content": "   "}
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_chat_falls_back_to_deterministic_on_llm_none(client, monkeypatch):
    """LLM returns None → deterministic reply is used instead."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    await _signup(client)
    await _seed_stock()
    _patch_orchestrator(monkeypatch)
    report = await _create_report(client)

    async def _none(*, question, report_payload, history):
        return None

    from services import llm_service

    monkeypatch.setattr(llm_service, "answer_chat_question", _none)
    res = await client.post(
        f"/api/chat/{report['id']}/message", json={"content": "What's the EPS?"}
    )
    body = res.json()["assistant_message"]["content"]
    assert "EPS" in body and "5.0" in body
