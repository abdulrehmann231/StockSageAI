"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

import os
import sys
import asyncio
from contextlib import asynccontextmanager

# Windows requires Selector event loop instead of ProactorEventLoop
# to avoid compatibility issues with SSL, subprocess, and third-party async libraries
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("Windows selector event loop policy enabled")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from api import (
    alerts as alerts_router,
    auth as auth_router,
    chat as chat_router,
    news as news_router,
    prices as prices_router,
    report as report_router,
    reports as reports_router,
    sentiment as sentiment_router,
    stocks as stocks_router,
    watchlist as watchlist_router,
)
from core.config import get_settings
from core.limiter import limiter
from core.logging import get_logger, setup_logging
from core.middleware import RequestIdMiddleware
from db.session import Base, engine
from services import cache_service

# Initialize logging and config before anything else
setup_logging()
settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Creates database schema on startup and cleans up connections on shutdown.
    """
    logger.info("Starting application", extra={"app_name": settings.app_name})
    print("Application starting...")

    # Ensure pgcrypto extension and create all ORM-mapped tables
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized")
    print("Database ready")
    yield

    # Cleanup resources on shutdown
    logger.info("Shutting down application")
    print("Shutting down...")
    await cache_service.close()
    await engine.dispose()

    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()
    print("Cleanup complete")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

print("Server is starting...")

# Request ID must be the outermost middleware so it's available everywhere
app.add_middleware(RequestIdMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS (Cross-Origin Resource Sharing) — controls which external domains can reach the API
# Production recommendation: keep origins list minimal, enumerate specific frontend URLs
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,  # List of allowed frontend domains
    allow_credentials=True,  # Allow cookies and credentials in cross-origin requests
    allow_methods=["*"],     # Allow all HTTP methods
    allow_headers=["*"],     # Allow all headers (auth tokens, content-type, etc.)
)

print("Middleware configured")

# Register all API routers
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

print("API routers registered")


@app.get("/")
async def root():
    """Root endpoint returning app metadata."""
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Liveness check - returns 200 if the process is running."""
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check - verifies database and Redis connectivity.

    Returns 200 if all dependencies are healthy, 503 if any are unavailable.
    This is used by container orchestrators (Docker, Kubernetes) to decide
    whether to route traffic to this instance.
    
    Difference from /health:
    - /health:                   liveness — just checks the process is running
    - /health/ready:             readiness — checks external dependencies
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    # Track dependency health — orchestrators examine this to make routing decisions
    checks = {"database": "unknown", "redis": "unknown"}

    # Check database connectivity with a lightweight SELECT 1 query
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))  # Minimal query to test connection
        checks["database"] = "healthy"
    except Exception as exc:
        # Database down means we cannot serve any requests that read/write data
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"

    # Check Redis connectivity — outage degrades performance but doesn't break core functionality
    try:
        redis = cache_service.get_redis()
        # PING command is the standard Redis healthcheck
        await redis.ping()
        checks["redis"] = "healthy"
    except Exception as exc:
        # Redis down means cache layer is unavailable, but app can still function
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"

    all_healthy = all(v == "healthy" for v in checks.values())

    # Return 503 if any dependency is down — tells load balancers to stop routing traffic here
    if not all_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    # 200 OK — all systems operational, safe to route traffic here
    return {"status": "healthy", "checks": checks}
