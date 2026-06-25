"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from api import alerts as alerts_router
from api import auth as auth_router
from api import chat as chat_router
from api import filings as filings_router
from api import news as news_router
from api import portfolio as portfolio_router
from api import prices as prices_router
from api import report as report_router
from api import reports as reports_router
from api import sentiment as sentiment_router
from api import stocks as stocks_router
from api import watchlist as watchlist_router

from core.config import get_settings
from core.limiter import limiter
from core.logging import setup_logging
from core.middleware import RequestIdMiddleware
from db.session import Base, engine
from services import cache_service

setup_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database schema on startup and cleans up connections on shutdown.

    Startup sequence:
        1. Log the boot event for operator visibility.
        2. Ensure the pgcrypto PostgreSQL extension exists.
        3. Create/update all database tables from SQLAlchemy ORM metadata.

    Shutdown sequence:
        1. Close the Redis cache connection pool.
        2. Dispose of the SQLAlchemy database engine (closes all DB connections).
        3. Close the headless browser pool used by scrapers (if active).
    """

    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
        await conn.run_sync(Base.metadata.create_all)

    yield

    await cache_service.close()

    await engine.dispose()

    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RequestIdMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(stocks_router.router)
app.include_router(prices_router.router)
app.include_router(sentiment_router.router)
app.include_router(news_router.router)
app.include_router(report_router.router)
app.include_router(reports_router.router)
app.include_router(chat_router.router)
app.include_router(watchlist_router.router)
app.include_router(alerts_router.router)
app.include_router(portfolio_router.router)
app.include_router(filings_router.router)


@app.get("/")
async def root():
    """Root endpoint returning app info.

    This is the most basic endpoint in the application. It returns the app
    name, version, and a status field. Useful for:
      - Quick smoke tests after deployment
      - API metadata discovery
      - Verifying the app is reachable and responding
    """
    response_data = {"app": settings.app_name, "version": "0.1.0", "status": "ok"}
    return response_data


@app.get("/health")
async def health():
    """Basic health check endpoint.

    Liveness endpoint: confirms the service process is running and the ASGI
    server is accepting connections. This does NOT check external dependencies
    (database, Redis, etc.) — that is the responsibility of /health/ready.

    Kubernetes and other orchestrators use this to decide if the pod should
    be restarted. If this endpoint fails, the process is considered dead.
    """
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies database and Redis connectivity.

    Returns 503 if any dependency is unavailable.

    While /health confirms the process is alive, /health/ready confirms the
    application can actually serve requests. It probes:
      1. PostgreSQL — via a lightweight SELECT 1 query
      2. Redis — via a PING command

    Orchestrators use this to control traffic routing: a pod that fails
    readiness is removed from the load balancer until it recovers.
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    checks = {"database": "unknown", "redis": "unknown"}

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception:
        checks["database"] = "unhealthy"

    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
    except Exception:
        checks["redis"] = "unhealthy"

    all_healthy = all(v == "healthy" for v in checks.values())

    if not all_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    return {"status": "healthy", "checks": checks}
