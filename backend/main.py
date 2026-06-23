"""FastAPI application entry point.

This module wires together the entire StockSageAI backend application:
    - Configures the asyncio event loop policy for Windows compatibility.
    - Initializes structured logging before anything else runs.
    - Builds the FastAPI app instance with middleware and routers.
    - Manages the database/cache/browser lifecycle via a lifespan handler.
    - Exposes basic root, liveness, and readiness health endpoints.
"""

# Standard library imports used for async setup and platform detection.
import asyncio
import sys
from contextlib import asynccontextmanager

# On Windows the default ProactorEventLoop does not play well with some
# async libraries (notably psycopg/asyncpg), so we switch to the selector
# based event loop policy before any loop is created.
if sys.platform == "win32":
    print("[startup] Windows platform detected — applying WindowsSelectorEventLoopPolicy")
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Third-party framework imports.
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

# Application API routers — each module groups a set of related endpoints.
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

# Core application infrastructure: settings, rate limiting, logging,
# request-id middleware, database session/engine, and the cache service.
from core.config import get_settings
from core.limiter import limiter
from core.logging import setup_logging
from core.middleware import RequestIdMiddleware
from db.session import Base, engine
from services import cache_service

# Configure logging as early as possible so that every subsequent log call
# (including those during app construction) uses the correct handlers/format.
print("[startup] Configuring logging")
setup_logging()

# Load and cache the application settings (env vars, defaults, etc.).
print("[startup] Loading settings")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database schema on startup and cleans up connections on shutdown.

    Startup sequence:
        1. Ensure the pgcrypto PostgreSQL extension exists.
        2. Create/update all database tables from SQLAlchemy ORM metadata.

    Shutdown sequence:
        1. Close the Redis cache connection pool.
        2. Dispose of the SQLAlchemy database engine (closes all DB connections).
        3. Close the headless browser pool used by scrapers (if active).
    """

    # --- Startup -------------------------------------------------------
    print("[lifespan] Startup: initializing database schema")
    async with engine.begin() as conn:
        # pgcrypto is required for UUID/crypto helpers used by the ORM models.
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        # Create any tables that do not yet exist based on ORM metadata.
        await conn.run_sync(Base.metadata.create_all)
    print("[lifespan] Startup complete — application ready")

    # Hand control back to the running application.
    yield

    # --- Shutdown ------------------------------------------------------
    print("[lifespan] Shutdown: closing cache connection")
    await cache_service.close()

    print("[lifespan] Shutdown: disposing database engine")
    await engine.dispose()

    # Imported lazily so the browser pool is only referenced when needed.
    print("[lifespan] Shutdown: closing browser pool")
    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()
    print("[lifespan] Shutdown complete")


# Build the FastAPI application instance with the lifespan handler attached.
print("[startup] Creating FastAPI application")
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# Attach a middleware that assigns/propagates a request id for tracing.
app.add_middleware(RequestIdMiddleware)

# Wire up rate limiting: store the limiter on app state and register the
# handler that converts RateLimitExceeded errors into HTTP 429 responses.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS so the frontend (and other allowed origins) can call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all feature routers under the application.
print("[startup] Registering routers")
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
print("[startup] Router registration complete")


@app.get("/")
async def root():
    """Root endpoint returning app info.

    Returns the app name, version, and a status field. Useful for quick smoke
    tests after deployment, API metadata discovery, and verifying the app is
    reachable and responding.
    """
    print("[request] GET / — root endpoint hit")
    # Assemble the metadata payload describing this service.
    response_data = {"app": settings.app_name, "version": "0.1.0", "status": "ok"}
    return response_data


@app.get("/health")
async def health():
    """Basic health check endpoint.

    Liveness endpoint: confirms the service process is running and the ASGI
    server is accepting connections. This does NOT check external dependencies
    (database, Redis, etc.) — that is the responsibility of /health/ready.
    """
    print("[request] GET /health — liveness probe")
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies database and Redis connectivity.

    While /health confirms the process is alive, /health/ready confirms the
    application can actually serve requests by probing PostgreSQL (SELECT 1)
    and Redis (PING). Returns 503 if any dependency is unavailable.
    """
    print("[request] GET /health/ready — readiness probe")
    # Imported lazily to keep module import time light.
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    # Track the health of each dependency individually.
    checks = {"database": "unknown", "redis": "unknown"}

    # Probe the database with a trivial query.
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
        print("[health] Database check passed")
    except Exception:
        checks["database"] = "unhealthy"
        print("[health] Database check FAILED")

    # Probe Redis with a PING.
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
        print("[health] Redis check passed")
    except Exception:
        checks["redis"] = "unhealthy"
        print("[health] Redis check FAILED")

    # The service is only ready if every dependency reports healthy.
    all_healthy = all(v == "healthy" for v in checks.values())

    # Surface a 503 so orchestrators stop routing traffic to this instance.
    if not all_healthy:
        print("[health] One or more dependencies unhealthy — returning 503")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    print("[health] All dependencies healthy")
    return {"status": "healthy", "checks": checks}
