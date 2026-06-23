"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

# Windows-specific event loop note:
# The selector policy tends to be more compatible with libraries that rely on
# socket-based async I/O. Setting it early avoids subtle runtime issues that
# can appear on Windows with the default proactor policy in some environments.
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

# Initialize logging before anything else
setup_logging()
print("[init] Logging configured.")

settings = get_settings()
logger = get_logger(__name__)
# Print once at import time so startup configuration is visible in local runs.
print(f"[init] Loaded settings for app: {settings.app_name}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database schema on startup and cleans up connections on shutdown.
    """
    # Startup phase overview:
    # 1. Announce boot in terminal/logs for quick operator visibility.
    # 2. Ensure required DB extension exists.
    # 3. Create/update database schema from SQLAlchemy metadata.
    # This centralizes one-time app initialization that should run before the
    # API starts accepting requests.
    print(f"[startup] Booting {settings.app_name}...")
    logger.info("Starting application", extra={"app_name": settings.app_name})

    # Ensure required DB extension and schema are available at startup.
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.run_sync(Base.metadata.create_all)

    print("[startup] Database initialization completed.")
    logger.info("Database initialized")
    print("[startup] Application is ready to accept requests.")
    logger.info("Application startup complete", extra={"app_name": settings.app_name, "version": "0.1.0"})
    yield

    # Shutdown phase overview:
    # Release external resources in a predictable order so in-flight operations
    # fail less noisily during termination. This also prevents connection leaks
    # when processes are recycled by orchestration platforms.
    print("[shutdown] Shutdown sequence started.")
    print("[shutdown] Releasing cache and database resources...")
    logger.info("Shutting down application")
    await cache_service.close()
    await engine.dispose()

    # Close browser pool if it was initialized
    from scrapers.browser_pool import close_browser_pool
    print("[shutdown] Closing browser pool (if active)...")
    await close_browser_pool()
    print("[shutdown] Application shutdown complete.")
    logger.info("Application shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
# App object creation happens at import, before serving traffic.
print("[init] FastAPI application instance created.")
logger.info("FastAPI app instance created", extra={"title": settings.app_name, "version": "0.1.0"})

# Middleware ordering note:
# Request ID middleware runs first so downstream middleware and route handlers
# can attach logs/traces to a stable request correlation ID. This improves
# debuggability across distributed components.
app.add_middleware(RequestIdMiddleware)
print("[init] RequestIdMiddleware registered.")

# Rate-limiting setup:
# The limiter is attached to app state for use by route decorators, and a
# dedicated exception handler translates quota violations into consistent HTTP
# responses instead of generic server errors.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
print("[init] Rate limiter and exception handler configured.")

# CORS policy note:
# Allowed origins come from configuration so environments can differ safely
# (local, staging, production). Credentials are enabled to support auth flows
# where cookies or authenticated browser requests are required.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print(f"[init] CORS configured for origins: {settings.cors_origins_list}")

# Router registration strategy:
# Each router encapsulates a domain area (auth, stocks, prices, sentiment, etc.)
# so route files stay focused and maintainable. Registering them here creates a
# single, clear composition root for the full HTTP surface of the backend.
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
print("[init] All API routers registered.")


@app.get("/")
async def root():
    """Root endpoint returning app info."""
    # Lightweight endpoint useful for quick smoke checks.
    print("[request] GET / called")
    logger.info("Root endpoint hit", extra={"method": "GET", "path": "/"})
    print("[request] GET / responding with app metadata.")
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Basic health check endpoint."""
    # Liveness endpoint: service process is running.
    print("[request] GET /health called")
    logger.info("Health endpoint hit", extra={"method": "GET", "path": "/health"})
    print("[request] GET /health responding healthy.")
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies database and Redis connectivity.

    Returns 503 if any dependency is unavailable.
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal
    # Readiness semantics:
    # This endpoint checks external dependencies that are required for normal
    # request processing. If any check fails, we return 503 so orchestrators can
    # keep the instance out of rotation until dependencies recover.
    print("[request] GET /health/ready called")

    checks = {"database": "unknown", "redis": "unknown"}
    # Track dependency-specific status so callers can quickly identify which
    # component is degraded without parsing logs.
    print("[health/ready] Running dependency readiness checks...")

    # Database probe:
    # Perform a lightweight query through the normal async session path.
    # This validates connectivity plus basic query execution capability.
    print("[health/ready] Checking database connectivity...")
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
        print("[health/ready] Database check passed.")
    except Exception as exc:
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"[health/ready] Database check failed: {exc}")

    # Redis probe:
    # Use ping as a minimal command to validate that the cache service is
    # reachable and responsive.
    print("[health/ready] Checking Redis connectivity...")
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
        print("[health/ready] Redis check passed.")
    except Exception as exc:
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        print(f"[health/ready] Redis check failed: {exc}")

    all_healthy = all(v == "healthy" for v in checks.values())
    # Emit a single summary line to simplify troubleshooting from container logs.
    print(f"[health/ready] Final checks: {checks}")

    if not all_healthy:
        # 503 indicates temporary unavailability due to dependency health, not
        # an application crash. This helps upstream systems make better decisions.
        print("[health/ready] Dependencies unhealthy, returning 503.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    print("[health/ready] All dependencies healthy.")
    return {"status": "healthy", "checks": checks}
