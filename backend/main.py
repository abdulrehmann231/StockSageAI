"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

# Windows compatibility: Use Selector event loop for Windows instead of the default ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

# API route routers for different features
from api import alerts as alerts_router
from api import auth as auth_router
from api import chat as chat_router
from api import news as news_router
from api import prices as prices_router
from api import report as report_router
from api import reports as reports_router
from api import sentiment as sentiment_router
from api import stocks as stocks_router
from api import watchlist as watchlist_router

# Core configuration and utilities
from core.config import get_settings
from core.limiter import limiter
from core.logging import get_logger, setup_logging
from core.middleware import RequestIdMiddleware

# Database setup
from db.session import Base, engine
from services import cache_service

# Initialize logging before anything else
setup_logging()

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database schema on startup and cleans up connections on shutdown.
    """
    logger.info("Starting application", extra={"app_name": settings.app_name})

    # Initialize database: create pgcrypto extension and set up all tables
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized")
    
    # Application is running - yield control back to FastAPI
    yield

    # Cleanup on shutdown
    logger.info("Shutting down application")
    await cache_service.close()
    await engine.dispose()

    # Close browser pool if it was initialized (used for web scraping)
    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware order matters - request ID should be first to be available in all other middleware
app.add_middleware(RequestIdMiddleware)

# Set up rate limiting with SlowAPI
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration - allow requests from frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/")
async def root():
    """Root endpoint returning app info."""
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Basic liveness check endpoint.
    
    Returns 200 if the application is running (doesn't verify dependencies).
    """
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies database and Redis connectivity.

    Returns 200 if all dependencies are healthy, 503 if any are unavailable.
    This endpoint is typically used by container orchestration systems (Docker, Kubernetes)
    to determine if the service is ready to handle traffic.
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    checks = {"database": "unknown", "redis": "unknown"}

    # Check database connectivity
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as exc:
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"

    # Check Redis connectivity (used for caching)
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
    except Exception as exc:
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"

    # Return 503 if any critical dependency is unhealthy
    all_healthy = all(v == "healthy" for v in checks.values())

    if not all_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    return {"status": "healthy", "checks": checks}
