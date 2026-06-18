"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
This is the main module that bootstraps the PSX (Pakistan Stock Exchange)
analytics backend service.

Architecture Overview:
    - FastAPI framework with async/await support
    - PostgreSQL database via SQLAlchemy async engine
    - Redis for caching and rate limiting
    - SlowAPI for API rate limiting
    - CORS enabled for frontend communication

Startup Sequence:
    1. Logging is configured first to capture all output
    2. Settings loaded from environment variables
    3. FastAPI app created with lifespan handler
    4. Middleware stack attached (RequestId -> RateLimit -> CORS)
    5. All API routers registered
    6. On startup: database schema initialized, extensions enabled
    7. Service ready to accept requests
"""

import asyncio
import sys
from contextlib import asynccontextmanager

# Fix for Windows event loop policy - Python on Windows defaults to
# ProactorEventLoop which doesn't support some async operations like
# subprocess creation and socket operations needed by SQLAlchemy
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("[main] Windows detected - set SelectorEventLoop policy")

# --- Core framework imports ---
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

# --- API route imports ---
# Each module defines a router with endpoints for a specific domain.
# Routers are namespaced under /api/v1/ via their individual configurations.
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

# --- Internal service imports ---
from core.config import get_settings      # Pydantic-based settings from env vars
from core.limiter import limiter           # SlowAPI rate limiter instance
from core.logging import get_logger, setup_logging  # Structured logging utilities
from core.middleware import RequestIdMiddleware      # Adds unique request ID to each request
from db.session import Base, engine        # SQLAlchemy Base and async engine
from services import cache_service         # Redis cache service

# --- Application initialization ---
# Logging must be set up first so all subsequent operations are captured
print("[main] Initializing logging configuration...")
setup_logging()

# Load settings from environment variables / .env file
# This includes database URL, Redis URL, CORS origins, API keys, etc.
print("[main] Loading application settings...")
settings = get_settings()
logger = get_logger(__name__)

print(f"[main] Application '{settings.app_name}' module loaded")
print(f"[main] Environment: {'development' if settings.debug else 'production'}")
print(f"[main] Database URL configured: {'Yes' if settings.database_url else 'No'}")
print(f"[main] Redis URL configured: {'Yes' if settings.redis_url else 'No'}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    This async context manager wraps the entire application lifecycle.
    Code before `yield` runs on startup; code after runs on shutdown.

    Startup tasks:
        - Initialize PostgreSQL extensions (pgcrypto for UUID generation)
        - Create all database tables if they don't exist
        - Verify database connectivity

    Shutdown tasks:
        - Close Redis connections gracefully
        - Dispose database connection pool
        - Close browser pool (used by web scrapers)

    Args:
        app: The FastAPI application instance

    Yields:
        Control to the application while it's running
    """
    print("=" * 60)
    print("[lifespan] ===== APPLICATION STARTING UP =====")
    print("=" * 60)
    logger.info("Starting application", extra={"app_name": settings.app_name})

    # Step 1: Initialize database schema
    # pgcrypto extension is needed for gen_random_uuid() in PostgreSQL
    print("[lifespan] Step 1/2: Initializing database schema...")
    try:
        async with engine.begin() as conn:
            # Enable pgcrypto for UUID generation across the application
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
            # Create all tables defined in SQLAlchemy models
            await conn.run_sync(Base.metadata.create_all)
        print("[lifespan] Database schema initialized successfully")
        logger.info("Database initialized")
    except Exception as exc:
        print(f"[lifespan] ERROR: Database initialization failed: {exc}")
        logger.error("Database initialization failed", extra={"error": str(exc)})
        raise

    # Step 2: Verify cache service is available
    print("[lifespan] Step 2/2: Verifying cache service...")
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        print("[lifespan] Cache service (Redis) is available")
    except Exception as exc:
        print(f"[lifespan] WARNING: Cache service unavailable: {exc}")
        logger.warning("Cache service unavailable on startup", extra={"error": str(exc)})

    print("=" * 60)
    print("[lifespan] ===== APPLICATION READY =====")
    print("=" * 60)

    # Application is now ready to serve requests
    # The `yield` transfers control to the running application
    yield

    # --- Shutdown phase ---
    # This code executes when the application receives a shutdown signal
    print("=" * 60)
    print("[lifespan] ===== APPLICATION SHUTTING DOWN =====")
    print("=" * 60)
    logger.info("Shutting down application")

    # Close Redis cache connection pool
    print("[lifespan] Closing cache service connections...")
    try:
        await cache_service.close()
        print("[lifespan] Cache service closed successfully")
    except Exception as exc:
        print(f"[lifespan] Warning: Error closing cache: {exc}")

    # Dispose SQLAlchemy database connection pool
    print("[lifespan] Disposing database engine connection pool...")
    try:
        await engine.dispose()
        print("[lifespan] Database engine disposed successfully")
    except Exception as exc:
        print(f"[lifespan] Warning: Error disposing engine: {exc}")

    # Close browser pool if it was initialized (used by web scrapers)
    print("[lifespan] Closing browser pool (if initialized)...")
    try:
        from scrapers.browser_pool import close_browser_pool
        await close_browser_pool()
        print("[lifespan] Browser pool closed successfully")
    except Exception as exc:
        print(f"[lifespan] Warning: Error closing browser pool: {exc}")

    print("=" * 60)
    print("[lifespan] ===== SHUTDOWN COMPLETE =====")
    print("=" * 60)


# --- Create the FastAPI application instance ---
# The lifespan handler manages startup/shutdown events
print("[main] Creating FastAPI application instance...")
app = FastAPI(
    title=settings.app_name,
    description="PSX Analytics API - Pakistan Stock Exchange data, sentiment analysis, and AI-powered reports",
    version="0.1.0",
    lifespan=lifespan,
)
print(f"[main] FastAPI app created: {settings.app_name} v0.1.0")

# --- Middleware configuration ---
# Middleware executes in reverse order of addition (last added = first executed)
# Request ID middleware should be outermost (first added) so it's available everywhere
print("[main] Configuring middleware stack...")

# 1. Request ID middleware - generates unique ID for each request for tracing
print("[main]   -> Adding RequestIdMiddleware (request tracing)")
app.add_middleware(RequestIdMiddleware)

# 2. Rate limiter - prevents API abuse with configurable limits per endpoint
print("[main]   -> Configuring rate limiter (SlowAPI)")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 3. CORS middleware - allows frontend (different origin) to call this API
#    Configured origins are loaded from settings (environment variable)
print(f"[main]   -> Adding CORS middleware (origins: {settings.cors_origins_list})")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,    # Allow cookies/auth headers
    allow_methods=["*"],       # Allow all HTTP methods
    allow_headers=["*"],       # Allow all request headers
)

print("[main] Middleware stack configured successfully")

# --- Route registration ---
# Each router is namespaced and handles a specific API domain.
# Routes are prefixed with /api/v1/ in the router configurations.
print("[main] Registering API routers...")
print("[main]   -> /auth       - Authentication & user management")
app.include_router(auth_router.router)
print("[main]   -> /stocks     - Stock listings & company details")
app.include_router(stocks_router.router)
print("[main]   -> /prices     - Price data & historical charts")
app.include_router(prices_router.router)
print("[main]   -> /sentiment  - Market sentiment analysis")
app.include_router(sentiment_router.router)
print("[main]   -> /news       - News aggregation & articles")
app.include_router(news_router.router)
print("[main]   -> /report     - Single report generation")
app.include_router(report_router.router)
print("[main]   -> /reports    - Report history & management")
app.include_router(reports_router.router)
print("[main]   -> /chat       - AI chat assistant")
app.include_router(chat_router.router)
print("[main]   -> /watchlist  - User watchlists")
app.include_router(watchlist_router.router)
print("[main]   -> /alerts     - Price & event alerts")
app.include_router(alerts_router.router)

print(f"[main] All 10 routers registered successfully")
print("[main] Application module initialization complete")


@app.get("/")
async def root():
    """Root endpoint returning application info.

    This is the simplest endpoint and serves as a quick smoke test
    to verify the service process is running and responsive.

    Returns:
        dict: Application name, version, and status

    Example response:
        {"app": "PSX Analytics", "version": "0.1.0", "status": "ok"}
    """
    print("[root] Root endpoint called - returning app info")
    print(f"[root] Responding with app={settings.app_name}, version=0.1.0")
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Basic liveness check endpoint.

    Returns HTTP 200 if the application process is alive and can handle requests.
    This does NOT verify downstream dependencies (database, Redis, etc.).

    Used by:
        - Container orchestration (Docker, K8s) for liveness probes
        - Load balancers to verify the instance is responding
        - Simple monitoring scripts

    Returns:
        dict: Status indicator

    Example response:
        {"status": "healthy"}
    """
    print("[health] Liveness check requested - process is alive")
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies all downstream dependencies.

    Unlike the basic /health endpoint, this actually tests connectivity
    to critical services (PostgreSQL database and Redis cache).

    Returns HTTP 200 only if ALL dependencies are healthy.
    Returns HTTP 503 if ANY dependency is unavailable.

    Used by:
        - Kubernetes readiness probes (routes traffic only when ready)
        - Load balancers (removes unhealthy instances from pool)
        - Deployment automation (waits for service to be ready)

    Returns:
        dict: Overall status and per-dependency health details

    Raises:
        HTTPException: 503 if any dependency check fails

    Example success response:
        {"status": "healthy", "checks": {"database": "healthy", "redis": "healthy"}}

    Example failure response (HTTP 503):
        {"status": "unhealthy", "checks": {"database": "healthy", "redis": "unhealthy"}}
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    print("[health_ready] ========================================")
    print("[health_ready] Readiness check initiated")
    print("[health_ready] ========================================")
    checks = {"database": "unknown", "redis": "unknown"}

    # --- Database check ---
    # Execute a simple query to verify PostgreSQL is reachable and responsive
    print("[health_ready] [1/2] Testing database connectivity...")
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
        print("[health_ready] [1/2] Database: HEALTHY - query executed successfully")
    except Exception as exc:
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"[health_ready] [1/2] Database: UNHEALTHY - {type(exc).__name__}: {exc}")

    # --- Redis check ---
    # Send PING command to verify Redis is reachable and responsive
    print("[health_ready] [2/2] Testing Redis connectivity...")
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
        print("[health_ready] [2/2] Redis: HEALTHY - PING succeeded")
    except Exception as exc:
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        print(f"[health_ready] [2/2] Redis: UNHEALTHY - {type(exc).__name__}: {exc}")

    # --- Evaluate overall status ---
    all_healthy = all(v == "healthy" for v in checks.values())
    print(f"[health_ready] Results: {checks}")
    print(f"[health_ready] Overall: {'HEALTHY' if all_healthy else 'UNHEALTHY'}")

    if not all_healthy:
        # Return 503 Service Unavailable with detailed check results
        print(f"[health_ready] Returning HTTP 503 - service not ready")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    print("[health_ready] Returning HTTP 200 - all systems operational")
    return {"status": "healthy", "checks": checks}
