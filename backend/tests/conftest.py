"""Shared pytest fixtures.

Loads backend/.env.test into the environment before any backend module
imports, creates the schema once per session, and truncates user/stock
rows between tests.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env.test", override=True)

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from db.session import Base, SessionLocal, engine  # noqa: E402
from main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def prepare_database():
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def clean_tables():
    """Truncate all tables between tests for isolation."""
    async with SessionLocal() as session:
        await session.execute(text("TRUNCATE TABLE users, stocks RESTART IDENTITY CASCADE"))
        await session.commit()
    yield


@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def reset_rate_limiter():
    """Clear slowapi state between tests so per-IP counts don't bleed."""
    from core.limiter import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def flush_redis():
    """Flush the test Redis db between tests.

    Resets the cached client so its connection pool binds to the
    current test's event loop (redis-py async pools are loop-bound).
    """
    from services import cache_service

    await cache_service.close()
    redis = cache_service.get_redis()
    await redis.flushdb()
    yield
    await cache_service.close()


@pytest_asyncio.fixture(loop_scope="function")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(loop_scope="function")
async def seed_stocks():
    """Seed a handful of stocks for endpoint tests."""
    from db.models import Stock

    rows = [
        Stock(ticker="ENGRO", name="Engro Corporation Limited", market="PSX", sector="Conglomerate", currency="PKR"),
        Stock(ticker="LUCK", name="Lucky Cement Limited", market="PSX", sector="Materials", currency="PKR"),
        Stock(ticker="AAPL", name="Apple Inc.", market="NASDAQ", sector="Technology", currency="USD"),
        Stock(ticker="MSFT", name="Microsoft Corporation", market="NASDAQ", sector="Technology", currency="USD"),
        Stock(ticker="JPM", name="JPMorgan Chase & Co.", market="NYSE", sector="Financials", currency="USD"),
    ]
    async with SessionLocal() as session:
        session.add_all(rows)
        await session.commit()
    return rows
