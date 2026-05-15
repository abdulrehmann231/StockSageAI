import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_stocks_returns_seeded(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks")
    assert res.status_code == 200
    tickers = {s["ticker"] for s in res.json()}
    assert {"ENGRO", "LUCK", "AAPL", "MSFT", "JPM"} <= tickers


@pytest.mark.asyncio
async def test_list_stocks_filter_by_market(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks", params={"market": "PSX"})
    assert res.status_code == 200
    rows = res.json()
    assert all(r["market"] == "PSX" for r in rows)
    assert {r["ticker"] for r in rows} == {"ENGRO", "LUCK"}


@pytest.mark.asyncio
async def test_list_stocks_returns_empty_when_no_data(client: AsyncClient):
    res = await client.get("/api/stocks")
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_search_by_ticker(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks/search", params={"q": "eng"})
    assert res.status_code == 200
    tickers = {s["ticker"] for s in res.json()}
    assert "ENGRO" in tickers


@pytest.mark.asyncio
async def test_search_by_company_name(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks/search", params={"q": "Lucky"})
    assert res.status_code == 200
    tickers = {s["ticker"] for s in res.json()}
    assert "LUCK" in tickers


@pytest.mark.asyncio
async def test_search_is_case_insensitive(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks/search", params={"q": "AAPL"})
    assert res.status_code == 200
    assert any(s["ticker"] == "AAPL" for s in res.json())

    res = await client.get("/api/stocks/search", params={"q": "aapl"})
    assert res.status_code == 200
    assert any(s["ticker"] == "AAPL" for s in res.json())


@pytest.mark.asyncio
async def test_search_rejects_empty_query(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks/search", params={"q": ""})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_stock_by_ticker(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks/AAPL")
    assert res.status_code == 200
    body = res.json()
    assert body["ticker"] == "AAPL"
    assert body["market"] == "NASDAQ"
    assert body["currency"] == "USD"


@pytest.mark.asyncio
async def test_get_stock_is_case_insensitive_on_path(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks/aapl")
    assert res.status_code == 200
    assert res.json()["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_get_unknown_stock_returns_404(client: AsyncClient, seed_stocks):
    res = await client.get("/api/stocks/NOPE")
    assert res.status_code == 404
