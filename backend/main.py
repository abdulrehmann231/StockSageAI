"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
This is the main module that bootstraps the PSX (Pakistan Stock Exchange)
analytics backend service.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

# Fix for Windows event loop policy - Python on Windows defaults to
# ProactorEventLoop which doesn't support some async operations
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

# Import all API route modules
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
from core.config import get_settings
from core.limiter import limiter
from core.logging import get_logger, setup_logging
from core.middleware import RequestIdMiddleware
from db.session import Base, engine
from services import cache_service

# Initialize logging before anything else to capture all subsequent log output
print("[main] Initializing logging configuration...")
setup_logging()

# Load application settings from environment / .env file
settings = get_settings()
logger = get_logger(__name__)

print(f"[main] Application '{settings.app_name}' module loaded")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database schema on startup and cleans up connections on shutdown.
    This context manager wraps the entire application lifecycle.
    """
    print("[lifespan] Application starting up...")
    logger.info("Starting application", extra={"app_name": settings.app_name})

    # Create database tables and enable pgcrypto extension for UUID generation
    print("[lifespan] Initializing database schema...")
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.run_sync(Base.metadata.create_all)

    print("[lifespan] Database initialized successfully")
    logger.info("Database initialized")

    # Application is now ready to serve requests
    yield

    # --- Shutdown phase ---
    print("[lifespan] Application shutting down...")
    logger.info("Shutting down application")

    # Close Redis cache connection
    print("[lifespan] Closing cache service...")
    await cache_service.close()

    # Dispose database connection pool
    print("[lifespan] Disposing database engine...")
    await engine.dispose()

    # Close browser pool if it was initialized (used by scrapers)
    from scrapers.browser_pool import close_browser_pool
    print("[lifespan] Closing browser pool...")
    await close_browser_pool()

    print("[lifespan] Shutdown complete")


# Create the FastAPI application instance
print("[main] Creating FastAPI application...")
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# --- Middleware configuration ---
# Middleware order matters - request ID should be first to be available in all other middleware
print("[main] Adding RequestId middleware...")
app.add_middleware(RequestIdMiddleware)

# Configure rate limiter to prevent API abuse
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware - allow frontend to communicate with this backend
print("[main] Configuring CORS middleware...")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Route registration ---
# Each router handles a specific domain of the API
print("[main] Registering API routers...")
app.include_router(auth_router.router)       # Authentication & user management
app.include_router(stocks_router.router)     # Stock listings & details
app.include_router(prices_router.router)     # Price data & historical prices
app.include_router(sentiment_router.router)  # Market sentiment analysis
app.include_router(news_router.router)       # News aggregation & articles
app.include_router(report_router.router)     # Single report generation
app.include_router(reports_router.router)    # Report history & management
app.include_router(chat_router.router)       # AI chat assistant
app.include_router(watchlist_router.router)  # User watchlists
app.include_router(alerts_router.router)     # Price & event alerts

print(f"[main] All {10} routers registered successfully")


@app.get("/")
async def root():
    """Root endpoint returning app info.

    Used as a simple smoke test to verify the service is running.
    """
    print("[root] Root endpoint hit")
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Basic health check endpoint.

    Returns 200 if the application process is alive.
    Does not verify downstream dependencies.
    """
    print("[health] Basic health check requested")
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies database and Redis connectivity.

    Returns 503 if any dependency is unavailable.
    Used by load balancers and orchestrators to determine if the service
    can accept traffic.
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    print("[health_ready] Readiness check initiated...")
    checks = {"database": "unknown", "redis": "unknown"}

    # Check database connectivity by running a simple query
    print("[health_ready] Checking database connection...")
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
        print("[health_ready] Database: healthy")
    except Exception as exc:
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"[health_ready] Database: unhealthy - {exc}")

    # Check Redis connectivity with a PING command
    print("[health_ready] Checking Redis connection...")
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
        print("[health_ready] Redis: healthy")
    except Exception as exc:
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        print(f"[health_ready] Redis: unhealthy - {exc}")

    # Determine overall health status
    all_healthy = all(v == "healthy" for v in checks.values())

    if not all_healthy:
        print(f"[health_ready] Service NOT ready: {checks}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    print("[health_ready] All checks passed - service is ready")
    return {"status": "healthy", "checks": checks}
