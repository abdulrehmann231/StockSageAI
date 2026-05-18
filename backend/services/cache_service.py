"""Async Redis wrapper for short-lived JSON caching.

Used for price quotes (60s TTL), news scrapes (30m), sentiment (2h),
and similar transient artifacts. Keys are simple strings; values are
JSON-serialized to keep this Redis-implementation-agnostic.
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from core.config import get_settings

_settings = get_settings()
_client: Redis | None = None


def get_redis() -> Redis:
    """Lazy singleton; safe to call from sync or async code."""
    global _client
    if _client is None:
        _client = Redis.from_url(_settings.redis_url, decode_responses=True)
    return _client


async def get_json(key: str) -> Any | None:
    raw = await get_redis().get(key)
    return json.loads(raw) if raw is not None else None


async def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    await get_redis().set(key, json.dumps(value, default=str), ex=ttl_seconds)


async def delete(key: str) -> int:
    return await get_redis().delete(key)


async def flush_namespace(prefix: str) -> int:
    """Delete every key starting with prefix. Uses SCAN; safe for prod."""
    redis = get_redis()
    count = 0
    async for k in redis.scan_iter(match=f"{prefix}*"):
        await redis.delete(k)
        count += 1
    return count


async def close() -> None:
    """Drop the cached client. Tolerates cross-loop errors so tests can reset."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            # Most often a "different event loop" complaint during teardown;
            # we still want to drop the reference so the next get_redis() rebuilds.
            pass
        _client = None
