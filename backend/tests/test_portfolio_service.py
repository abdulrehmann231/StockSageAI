"""Offline tests for the Phase-7 portfolio service, analyst agent, and snapshot
worker. Price fetching is stubbed so nothing here touches the network."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from agents import portfolio_analyst_agent
from db.models import Holding, Stock
from db.schemas import HoldingOut, PriceQuote
from services import portfolio_service


def _stock(ticker, market="NASDAQ", sector="Technology", currency="USD", name=None):
    return Stock(
        ticker=ticker,
        name=name or f"{ticker} Inc.",
        market=market,
        sector=sector,
        currency=currency,
    )


def _holding(ticker, qty, avg, *, market_stock, buy_date=None, hid=None):
    h = Holding(
        id=hid or uuid.uuid4(),
        user_id=uuid.uuid4(),
        ticker=ticker,
        quantity=qty,
        avg_buy_price=avg,
        buy_date=buy_date,
        is_active=True,
    )
    return h, market_stock


def _price(ticker, price, *, market="NASDAQ", is_delisted=False):
    return PriceQuote(
        ticker=ticker,
        market=market,
        price=price,
        is_delisted=is_delisted,
        fetched_at=datetime.now(timezone.utc),
        source="test",
    )


# --------------------------------------------------------------------------- #
# Enrichment + metrics
# --------------------------------------------------------------------------- #


def test_enrich_one_computes_pnl():
    stock = _stock("AAPL")
    holding, _ = _holding("AAPL", 10, 100.0, market_stock=stock)
    out = portfolio_service._enrich_one(holding, stock, _price("AAPL", 150.0))
    assert out.cost_basis == 1000.0
    assert out.current_value == 1500.0
    assert out.gain_loss == 500.0
    assert out.gain_loss_pct == 50.0
    assert out.price_error is None


def test_enrich_one_handles_missing_price():
    stock = _stock("AAPL")
    holding, _ = _holding("AAPL", 10, 100.0, market_stock=stock)
    out = portfolio_service._enrich_one(holding, stock, RuntimeError("boom"))
    assert out.current_value is None
    assert out.gain_loss is None
    assert "boom" in out.price_error


def test_compute_metrics_aggregates_and_finds_best_worst():
    holdings = [
        HoldingOut(
            id=uuid.uuid4(), ticker="AAPL", market="NASDAQ", sector="Technology",
            quantity=10, avg_buy_price=100, is_active=True, cost_basis=1000.0,
            current_value=1500.0, gain_loss=500.0, gain_loss_pct=50.0,
        ),
        HoldingOut(
            id=uuid.uuid4(), ticker="JPM", market="NYSE", sector="Financials",
            quantity=5, avg_buy_price=100, is_active=True, cost_basis=500.0,
            current_value=400.0, gain_loss=-100.0, gain_loss_pct=-20.0,
        ),
    ]
    m = portfolio_service.compute_metrics(holdings)
    assert m.total_value == 1900.0
    assert m.total_cost_basis == 1500.0
    assert m.total_gain_loss == 400.0
    assert m.best_performer["ticker"] == "AAPL"
    assert m.worst_performer["ticker"] == "JPM"
    # 1500/1900 ≈ 78.9% tech, 400/1900 ≈ 21.1% financials
    assert round(m.sector_allocation["Technology"]) == 79
    assert m.priced_count == 2


def test_compute_metrics_empty():
    m = portfolio_service.compute_metrics([])
    assert m.total_value == 0.0
    assert m.holdings_count == 0


def test_compute_metrics_ignores_unpriced_in_value():
    holdings = [
        HoldingOut(
            id=uuid.uuid4(), ticker="AAPL", market="NASDAQ", sector="Technology",
            quantity=10, avg_buy_price=100, is_active=True, cost_basis=1000.0,
            current_value=1500.0, gain_loss=500.0, gain_loss_pct=50.0,
        ),
        HoldingOut(
            id=uuid.uuid4(), ticker="DEAD", market="PSX", sector="Materials",
            quantity=5, avg_buy_price=100, is_active=True, cost_basis=500.0,
            price_error="no price",
        ),
    ]
    m = portfolio_service.compute_metrics(holdings)
    assert m.total_value == 1500.0
    assert m.priced_count == 1
    assert m.holdings_count == 2


@pytest.mark.asyncio
async def test_build_portfolio_stubs_price(monkeypatch):
    stock = _stock("AAPL")
    rows = [_holding("AAPL", 10, 100.0, market_stock=stock)]

    async def fake_get_price(ticker, market, *, use_cache=True):
        return _price(ticker, 120.0)

    monkeypatch.setattr(portfolio_service.price_agent, "get_price", fake_get_price)
    holdings, metrics, errors = await portfolio_service.build_portfolio(rows)
    assert errors == []
    assert holdings[0].current_value == 1200.0
    assert metrics.total_gain_loss == 200.0


@pytest.mark.asyncio
async def test_build_portfolio_isolates_price_failure(monkeypatch):
    stock = _stock("AAPL")
    rows = [_holding("AAPL", 10, 100.0, market_stock=stock)]

    async def boom(ticker, market, *, use_cache=True):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(portfolio_service.price_agent, "get_price", boom)
    holdings, metrics, errors = await portfolio_service.build_portfolio(rows)
    assert len(errors) == 1
    assert holdings[0].current_value is None
    assert metrics.priced_count == 0


# --------------------------------------------------------------------------- #
# Tax estimation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tax_estimate_psx_short_vs_long_term(monkeypatch):
    today = date(2026, 6, 25)
    short = _stock("ENGRO", market="PSX", currency="PKR")
    long = _stock("LUCK", market="PSX", currency="PKR")
    rows = [
        _holding("ENGRO", 100, 100.0, market_stock=short, buy_date=date(2026, 3, 1)),  # <1y
        _holding("LUCK", 100, 100.0, market_stock=long, buy_date=date(2024, 1, 1)),    # >1y
    ]

    async def fake_get_price(ticker, market, *, use_cache=True):
        return _price(ticker, 150.0, market="PSX")

    monkeypatch.setattr(portfolio_service.price_agent, "get_price", fake_get_price)
    out = await portfolio_service.estimate_tax(rows, today=today)
    lots = {lot.ticker: lot for lot in out.lots}
    # ENGRO: gain 5000, short-term PSX rate 15% → 750
    assert lots["ENGRO"].tax_rate_pct == 15.0
    assert lots["ENGRO"].estimated_tax == 750.0
    # LUCK: gain 5000, long-term PSX rate 12.5% → 625
    assert lots["LUCK"].tax_rate_pct == 12.5
    assert lots["LUCK"].estimated_tax == 625.0
    assert out.total_estimated_tax == 1375.0


@pytest.mark.asyncio
async def test_tax_estimate_loss_is_harvest_opportunity(monkeypatch):
    today = date(2026, 6, 25)
    stock = _stock("AAPL")
    rows = [_holding("AAPL", 10, 200.0, market_stock=stock, buy_date=date(2026, 1, 1))]

    async def fake_get_price(ticker, market, *, use_cache=True):
        return _price(ticker, 150.0)

    monkeypatch.setattr(portfolio_service.price_agent, "get_price", fake_get_price)
    out = await portfolio_service.estimate_tax(rows, today=today)
    lot = out.lots[0]
    assert lot.estimated_tax == 0.0
    assert "harvest" in lot.note.lower()


@pytest.mark.asyncio
async def test_tax_estimate_flags_near_long_term_threshold(monkeypatch):
    today = date(2026, 6, 25)
    stock = _stock("AAPL")
    # bought 350 days ago → 15 days from long-term
    rows = [_holding("AAPL", 10, 100.0, market_stock=stock, buy_date=date(2025, 7, 10))]

    async def fake_get_price(ticker, market, *, use_cache=True):
        return _price(ticker, 150.0)

    monkeypatch.setattr(portfolio_service.price_agent, "get_price", fake_get_price)
    out = await portfolio_service.estimate_tax(rows, today=today)
    lot = out.lots[0]
    assert lot.near_long_term_threshold is True
    assert lot.tax_rate_pct == portfolio_service.US_SHORT_TERM_RATE


def test_tax_rate_for():
    assert portfolio_service.tax_rate_for("PSX", True) == 12.5
    assert portfolio_service.tax_rate_for("PSX", False) == 15.0
    assert portfolio_service.tax_rate_for("NYSE", True) == 15.0
    assert portfolio_service.tax_rate_for("NYSE", False) == 22.0


# --------------------------------------------------------------------------- #
# Portfolio Analyst Agent (deterministic, no LLM key)
# --------------------------------------------------------------------------- #


def _out(ticker, value, cost, *, sector="Technology", market="NASDAQ", delisted=False):
    gl = (value - cost) if value is not None else None
    return HoldingOut(
        id=uuid.uuid4(), ticker=ticker, market=market, sector=sector,
        quantity=1, avg_buy_price=cost, is_active=True, cost_basis=cost,
        current_value=value, gain_loss=gl,
        gain_loss_pct=(gl / cost * 100.0) if (gl is not None and cost) else None,
        is_delisted=delisted,
    )


@pytest.mark.asyncio
async def test_analyst_flags_concentration(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    holdings = [
        _out("AAPL", 9000.0, 6000.0),
        _out("JPM", 1000.0, 1000.0, sector="Financials", market="NYSE"),
    ]
    metrics = portfolio_service.compute_metrics(holdings)
    analysis = await portfolio_analyst_agent.analyze_portfolio(
        holdings, metrics, risk_profile="Moderate"
    )
    assert 0 <= analysis["health_score"] <= 100
    assert any("AAPL" in w for w in analysis["concentration_warnings"])
    assert analysis["model_used"] is None


@pytest.mark.asyncio
async def test_analyst_flags_tax_loss_and_delisting(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    holdings = [
        _out("AAPL", 800.0, 1000.0),  # -20% loss
        _out("DEAD", 500.0, 500.0, market="PSX", sector="Materials", delisted=True),
    ]
    metrics = portfolio_service.compute_metrics(holdings)
    analysis = await portfolio_analyst_agent.analyze_portfolio(holdings, metrics)
    assert any("AAPL" in t for t in analysis["tax_loss_opportunities"])
    assert any("DEAD" in w for w in analysis["weaknesses"])


@pytest.mark.asyncio
async def test_analyst_llm_path_used_when_available(monkeypatch):
    holdings = [_out("AAPL", 1500.0, 1000.0)]
    metrics = portfolio_service.compute_metrics(holdings)

    async def fake_llm(*, payload):
        return {
            "health_score": 88,
            "summary": "Strong, focused tech portfolio.",
            "strengths": ["Up nicely"],
            "weaknesses": ["Single sector"],
            "recommendations": ["Diversify"],
            "_model": "test-model",
        }

    monkeypatch.setattr(portfolio_analyst_agent.llm_service, "analyze_portfolio", fake_llm)
    analysis = await portfolio_analyst_agent.analyze_portfolio(holdings, metrics)
    assert analysis["health_score"] == 88
    assert analysis["model_used"] == "test-model"
    assert analysis["summary"].startswith("Strong")
