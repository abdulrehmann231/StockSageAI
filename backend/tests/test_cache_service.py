"""Integration tests against the test Redis db (index 15)."""

import pytest

from services import cache_service


@pytest.mark.asyncio
async def test_set_and_get_round_trip():
    await cache_service.set_json("test:hello", {"a": 1, "b": "two"}, ttl_seconds=60)
    val = await cache_service.get_json("test:hello")
    assert val == {"a": 1, "b": "two"}


@pytest.mark.asyncio
async def test_get_missing_returns_none():
    assert await cache_service.get_json("test:does-not-exist") is None


@pytest.mark.asyncio
async def test_delete_removes_key():
    await cache_service.set_json("test:to-delete", {"x": 1}, ttl_seconds=60)
    assert await cache_service.get_json("test:to-delete") == {"x": 1}

    removed = await cache_service.delete("test:to-delete")
    assert removed == 1
    assert await cache_service.get_json("test:to-delete") is None


@pytest.mark.asyncio
async def test_flush_namespace_deletes_only_matching_keys():
    await cache_service.set_json("price:AAPL", {"p": 1}, ttl_seconds=60)
    await cache_service.set_json("price:MSFT", {"p": 2}, ttl_seconds=60)
    await cache_service.set_json("news:AAPL", {"n": 3}, ttl_seconds=60)

    removed = await cache_service.flush_namespace("price:")
    assert removed == 2
    assert await cache_service.get_json("price:AAPL") is None
    assert await cache_service.get_json("price:MSFT") is None
    # Unrelated namespace untouched.
    assert await cache_service.get_json("news:AAPL") == {"n": 3}
