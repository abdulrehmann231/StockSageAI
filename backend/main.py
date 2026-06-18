"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.

This module is intentionally small and focused: it wires together the
application lifecycle (startup/shutdown), middleware, routers, and basic
health endpoints. Keep heavy business logic in dedicated modules under
`api/`, `agents/`, or `services/` to keep this file easy to reason about.
"""

# ---------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------
import os
import sys
import asyncio
from contextlib import asynccontextmanager

# Log early platform information for easier debugging during local dev
print(f"[INIT] Starting on platform: {sys.platform}")

# On Windows, the default Proactor event loop can cause compatibility
# issues with some third-party asyncio libraries (SSL/subprocess). Use the
# selector policy which is more compatible for this project's async stack.
if sys.platform == "win32":
    print("[INIT] Configuring Windows event loop...")
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("[INIT] Windows selector event loop policy enabled")

# ---------------------------------------------------------------
# Third-party imports
# ---------------------------------------------------------------
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

# ---------------------------------------------------------------
# Application imports - API routers
# ---------------------------------------------------------------
from api import (
    alerts as alerts_router,    # Price alert endpoints
    auth as auth_router,        # Authentication endpoints
    chat as chat_router,        # Chat/assistant endpoints
    news as news_router,        # News aggregation endpoints
    prices as prices_router,    # Price data endpoints
    report as report_router,    # Single report endpoint
    reports as reports_router,  # Report history endpoints
    sentiment as sentiment_router,  # Sentiment analysis endpoints
    stocks as stocks_router,    # Stock search endpoints
    watchlist as watchlist_router,  # Watchlist CRUD endpoints
)

print("[INIT] Setting up logging and configuration...")
setup_logging()
print("[INIT] Logging initialized")
settings = get_settings()
print(f"[INIT] Configuration loaded: {settings.app_name}")
logger = get_logger(__name__)
print("[INIT] Logger acquired")


# ---------------------------------------------------------------
# Application lifecycle: startup & shutdown
# FastAPI calls lifespan enter on startup, exit on shutdown
# ---------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Startup tasks:
    - Ensure Postgres extensions required by the app exist (pgcrypto)
    - Create ORM tables if missing (safe no-op when already created)

    Shutdown tasks:
    - Close cache/Redis connections
    - Dispose DB engine pool
    - Close any browser pools used by scrapers

    Keeping these tasks in a single lifespan handler gives FastAPI a clean
    way to manage resources across the whole app process.
    """
    logger.info("Starting application", extra={"app_name": settings.app_name})
    print("[STARTUP] Application starting...")
    print("[STARTUP] Initializing database...")

    # Ensure pgcrypto extension and create all ORM-mapped tables. The
    # `CREATE EXTENSION` command is idempotent; running it on startup is a
    # simple way to ensure UUID functions (gen_random_uuid) work in dev and
    # production without a separate migration step.
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        print("[STARTUP] pgcrypto extension ready")
        # Create any missing tables based on SQLAlchemy models. This is a
        # convenience for development; production deployments should use a
        # proper migration workflow (eg. Alembic) when schema changes.
        await conn.run_sync(Base.metadata.create_all)
        print("[STARTUP] ORM tables synchronized")

    logger.info("Database initialized")
    print("[STARTUP] Database ready")
    print("[STARTUP] Application is now accepting requests")
    yield

    # Cleanup resources on shutdown: close cache first, then database, and
    # finally any external resources such as browser pools. Order matters to
    # avoid race conditions during graceful termination.
    logger.info("Shutting down application")
    print("[SHUTDOWN] Shutting down...")
    print("[SHUTDOWN] Closing Redis cache...")
    await cache_service.close()
    print("[SHUTDOWN] Redis cache closed")
    print("[SHUTDOWN] Disposing database engine...")
    await engine.dispose()
    print("[SHUTDOWN] Database engine disposed")

    print("[SHUTDOWN] Closing browser pool...")
    # Close Playwright/selenium/browser pools used by scrapers to avoid
    # leaving orphaned browser processes on the host machine.
    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()
    print("[SHUTDOWN] Browser pool closed")
    print("[SHUTDOWN] Cleanup complete")


print("[INIT] Creating FastAPI app instance...")
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    # Provide the lifespan manager so FastAPI runs our startup/shutdown
    # logic automatically when the server process begins and ends.
    lifespan=lifespan,
)
print("[INIT] App created")
print("[INIT] Configuring middleware stack...")

# ---------------------------------------------------------------
# Middleware configuration
# Order matters: first added = outermost (wraps all inner layers)
# ---------------------------------------------------------------

# `RequestIdMiddleware` should wrap the request early so the generated
# request id is available to all downstream handlers and loggers.
print("[INIT] Adding Request ID middleware...")
app.add_middleware(RequestIdMiddleware)
print("[INIT] Request ID middleware added")

# Rate limiter is attached to the app state and a global exception handler
# is registered to translate rate-limit errors into proper HTTP responses.
print("[INIT] Configuring rate limiter...")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
print("[INIT] Rate limiter configured")

# Configure CORS to allow the frontend application to talk to this API.
print(f"[INIT] Configuring CORS with origins: {settings.cors_origins_list}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print("[INIT] CORS middleware added")

print("[INIT] Middleware configuration complete")

# ---------------------------------------------------------------
# Route registration
# Each router handles a distinct API path prefix
# ---------------------------------------------------------------
print("[INIT] Registering API routers...")
# Register routers from the `api` package. Keeping routers small and
# focused (one per resource) helps maintain clear request/response schemas
# and keeps the main app wiring simple.
app.include_router(auth_router.router)
print("[INIT]   - Auth routes registered")
app.include_router(stocks_router.router)
print("[INIT]   - Stocks routes registered")
app.include_router(prices_router.router)
print("[INIT]   - Prices routes registered")
app.include_router(sentiment_router.router)
print("[INIT]   - Sentiment routes registered")
app.include_router(news_router.router)
print("[INIT]   - News routes registered")
app.include_router(report_router.router)
print("[INIT]   - Report routes registered")
app.include_router(reports_router.router)
print("[INIT]   - Reports routes registered")
app.include_router(chat_router.router)
print("[INIT]   - Chat routes registered")
app.include_router(watchlist_router.router)
print("[INIT]   - Watchlist routes registered")
app.include_router(alerts_router.router)
print("[INIT]   - Alerts routes registered")
print("[INIT] All API routers registered")


# ---------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------


@app.get("/")
async def root():
    """Root endpoint returning app metadata.

    Keep this lightweight so health checks and basic uptime probes stay fast.
    """
    print("[API] GET / — root endpoint called")
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Liveness check - returns 200 if the process is running.

    This endpoint verifies the process is alive. It should be very cheap
    and not perform any external I/O to avoid false negatives from slow
    dependencies.
    """
    print("[API] GET /health — liveness check")
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check - verifies database and Redis connectivity.

    Returns 200 if all dependencies are healthy, 503 if any are unavailable.
    Used by container orchestrators to decide whether to route traffic here.
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    print("[API] GET /health/ready — readiness check started")
    checks = {"database": "unknown", "redis": "unknown"}

    # Check database connectivity with a minimal, fast query. Using a
    # lightweight `SELECT 1` avoids loading large transactions or models.
    print("[API]   Checking database...")
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
        print("[API]   Database: healthy")
    except Exception as exc:
        # Log the error so operators can triage the failure quickly.
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"[API]   Database: unhealthy — {exc}")

    # Check Redis connectivity. Redis is primarily used for caching and
    # rate-limiting; an outage is important but not necessarily fatal for
    # read-only endpoints depending on design.
    print("[API]   Checking Redis...")
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
        print("[API]   Redis: healthy")
    except Exception as exc:
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        print(f"[API]   Redis: unhealthy — {exc}")

    all_healthy = all(v == "healthy" for v in checks.values())
    print(f"[API]   Result: {checks}")

    if not all_healthy:
        print(f"[API]   Readiness FAILED — returning 503")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    print("[API]   Readiness PASSED")
    return {"status": "healthy", "checks": checks}
