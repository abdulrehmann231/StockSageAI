from datetime import datetime, timezone

import pytest

from agents import price_agent
from db.models import Stock
from db.session import SessionLocal


@pytest.fixture
def fake_global(monkeypatch):
    """Replace yfinance with a deterministic stub."""

    async def _stub(ticker: str, market: str):
        return {
            "ticker": ticker.upper(),
            "market": market,
            "currency": "USD",
            "price": 187.50,
            "previous_close": 185.00,
            "open": 186.00,
            "day_high": 188.20,
            "day_low": 184.75,
            "volume": 50_000_000,
            "week_52_high": 220.00,
            "week_52_low": 150.00,
            "market_cap": 3_000_000_000_000,
            "pe_ratio": 32.5,
            "eps": 5.75,
            "dividend_yield": 0.0045,
            "change": 2.50,
            "change_pct": 1.35,
        }

    monkeypatch.setattr(price_agent, "_fetch_global_quote", _stub)
    return _stub


@pytest.fixture
def fake_psx(monkeypatch):
    async def _stub(ticker: str):
        return {
            "ticker": ticker.upper(),
            "market": "PSX",
            "currency": "PKR",
            "price": 312.45,
            "previous_close": 305.10,
            "open": 306.00,
            "day_high": 314.00,
            "day_low": 304.50,
            "volume": 1_200_000,
            "week_52_high": 380.00,
            "week_52_low": 220.00,
            "market_cap": 180_000_000_000,
            "pe_ratio": 8.1,
            "eps": 38.50,
            "dividend_yield": 0.07,
            "change": 7.35,
            "change_pct": 2.41,
        }

    monkeypatch.setattr(price_agent, "_fetch_psx_quote", _stub)
    return _stub


@pytest.mark.asyncio
async def test_get_price_global_uses_yfinance_path(fake_global):
    quote = await price_agent.get_price("AAPL", "NASDAQ")

    assert quote.ticker == "AAPL"
    assert quote.market == "NASDAQ"
    assert quote.currency == "USD"
    assert quote.price == 187.50
    assert quote.source == "yfinance"
    assert quote.cached is False
    assert isinstance(quote.fetched_at, datetime)


@pytest.mark.asyncio
async def test_get_price_psx_uses_scraper_path(fake_psx):
    quote = await price_agent.get_price("ENGRO", "PSX")

    assert quote.ticker == "ENGRO"
    assert quote.market == "PSX"
    assert quote.currency == "PKR"
    assert quote.price == 312.45
    assert quote.source == "psx"
    assert quote.cached is False


@pytest.mark.asyncio
async def test_get_price_second_call_serves_from_cache(fake_global, monkeypatch):
    first = await price_agent.get_price("AAPL", "NASDAQ")
    assert first.cached is False

    # Mark the fetcher so we can prove it wasn't called again.
    call_count = {"n": 0}

    async def counting_stub(*args, **kwargs):
        call_count["n"] += 1
        return await fake_global(*args, **kwargs)

    monkeypatch.setattr(price_agent, "_fetch_global_quote", counting_stub)

    second = await price_agent.get_price("AAPL", "NASDAQ")
    assert second.cached is True
    assert second.price == 187.50
    assert call_count["n"] == 0


@pytest.mark.asyncio
async def test_get_price_use_cache_false_bypasses_cache(fake_global, monkeypatch):
    await price_agent.get_price("AAPL", "NASDAQ")

    call_count = {"n": 0}

    async def counting_stub(*args, **kwargs):
        call_count["n"] += 1
        return await fake_global(*args, **kwargs)

    monkeypatch.setattr(price_agent, "_fetch_global_quote", counting_stub)

    fresh = await price_agent.get_price("AAPL", "NASDAQ", use_cache=False)
    assert fresh.cached is False
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_get_price_normalizes_ticker_to_upper(fake_global):
    quote = await price_agent.get_price("aapl", "NASDAQ")
    assert quote.ticker == "AAPL"


# ---------- HTTP endpoint ----------


@pytest.mark.asyncio
async def test_price_endpoint_returns_quote(client, fake_global):
    async with SessionLocal() as session:
        session.add(
            Stock(ticker="AAPL", name="Apple Inc.", market="NASDAQ", currency="USD")
        )
        await session.commit()

    res = await client.get("/api/stocks/AAPL/price")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ticker"] == "AAPL"
    assert body["source"] == "yfinance"
    assert body["price"] == 187.50


@pytest.mark.asyncio
async def test_price_endpoint_404_for_unknown_ticker(client):
    res = await client.get("/api/stocks/NOPE/price")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_price_endpoint_502_when_upstream_fails(client, monkeypatch):
    async with SessionLocal() as session:
        session.add(
            Stock(ticker="AAPL", name="Apple Inc.", market="NASDAQ", currency="USD")
        )
        await session.commit()

    async def broken(*_args, **_kwargs):
        raise ValueError("yfinance returned no data")

    monkeypatch.setattr(price_agent, "_fetch_global_quote", broken)

    res = await client.get("/api/stocks/AAPL/price")
    assert res.status_code == 502
    assert "yfinance returned no data" in res.json()["detail"]


@pytest.mark.asyncio
async def test_price_endpoint_dispatches_psx_for_psx_ticker(client, fake_psx):
    async with SessionLocal() as session:
        session.add(
            Stock(ticker="ENGRO", name="Engro Corporation", market="PSX", currency="PKR")
        )
        await session.commit()

    res = await client.get("/api/stocks/ENGRO/price")
    assert res.status_code == 200
    body = res.json()
    assert body["market"] == "PSX"
    assert body["currency"] == "PKR"
    assert body["source"] == "psx"
