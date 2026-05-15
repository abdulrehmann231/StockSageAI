import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root(client: AsyncClient):
    res = await client.get("/")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "app" in body
    assert "version" in body


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "healthy"}
