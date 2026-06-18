# StockSageAI - Backend server entry point
"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

import asyncio
import sys
from contextlib import asynccontextmanager  # Provides utilities for managing async setup/teardown

# Windows compatibility: Use Selector event loop instead of the default ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Third-party imports
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

# Load application configuration from environment variables
settings = get_settings()
logger = get_logger(__name__)

print(f"🚀 Starting {settings.app_name} v0.1.0")
print(f"🌐 Running on platform: {sys.platform}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database schema on startup and cleans up connections on shutdown.
    """
    logger.info("Starting application", extra={"app_name": settings.app_name})
    print("🧩 Entering application lifespan startup phase")

    # Initialize database: create pgcrypto extension and set up all tables
    async with engine.begin() as conn:
        print("📊 Initializing database schema...")
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized")
    print("✅ Database ready")
    
    # Application is running - yield control back to FastAPI
    yield

    # Cleanup on shutdown
    logger.info("Shutting down application")
    print("🛑 Shutting down application...")
    print("🧹 Releasing cache and database resources")
    await cache_service.close()
    await engine.dispose()

    # Close browser pool if it was initialized (used for web scraping)
    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()
    print("✅ Cleanup complete")


# Create the FastAPI application instance
app = FastAPI(
    title=settings.app_name,  # Application name from configuration
    version="0.1.0",           # Current API version
    lifespan=lifespan,         # Startup/shutdown lifecycle handler
)

# This print confirms module-level startup execution when the app process boots.
print("Server is starting...")

# Middleware order matters - request ID should be first to be available in all other middleware
app.add_middleware(RequestIdMiddleware)
print("Request ID middleware added")

# Set up rate limiting with SlowAPI
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
print("Rate limiter configured")

# CORS configuration - allow requests from frontend origins
# Keep this broad only in trusted environments; tighten in production where possible.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print("CORS middleware configured")

# Register all API routers
print("Registering API routers...")
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
print("All API routers registered")
print("✅ Application bootstrapping complete")


@app.get("/")
async def root():
    """Root endpoint returning app info."""
    # Useful as a quick sanity endpoint for reverse proxy and app wiring checks.
    print("Root endpoint hit")
    # Return basic application metadata
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Basic liveness check endpoint.
    
    Returns 200 if the application is running (doesn't verify dependencies).
    """
    print("Health check (liveness) requested")
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

    # Track dependency health in a single response object for clear diagnostics.
    checks = {"database": "unknown", "redis": "unknown"}
    # Readiness probes are usually called by orchestrators before routing traffic.
    print("🔎 Readiness check requested")

    # Check database connectivity
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
        print("✅ Database check: OK")
    except Exception as exc:
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"❌ Database check failed: {exc}")

    # Check Redis connectivity (used for caching)
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
        print("✅ Redis check: OK")
    except Exception as exc:
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        print(f"❌ Redis check failed: {exc}")

    # Return 503 if any critical dependency is unhealthy
    all_healthy = all(v == "healthy" for v in checks.values())
    # Keep this summary log for quick triage during startup incidents.
    print(f"📋 Readiness summary: {checks}")

    # Return 503 if any dependency is down
    if not all_healthy:
        print(f"⚠️ Readiness check failed: {checks}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    print("💚 All systems ready")
    return {"status": "healthy", "checks": checks}
