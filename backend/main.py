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

settings = get_settings()
logger = get_logger(__name__)
print(f"[init] Loaded settings for app: {settings.app_name}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database schema on startup and cleans up connections on shutdown.
    """
    # Startup visibility in local/dev runs before structured logs are inspected.
    print(f"[startup] Booting {settings.app_name}...")
    logger.info("Starting application", extra={"app_name": settings.app_name})

    # Ensure required DB extension and schema are available at startup.
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.run_sync(Base.metadata.create_all)

    print("[startup] Database initialization completed.")
    logger.info("Database initialized")
    yield

    # Graceful shutdown of external resources.
    print("[shutdown] Releasing cache and database resources...")
    logger.info("Shutting down application")
    await cache_service.close()
    await engine.dispose()

    # Close browser pool if it was initialized
    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()
    print("[shutdown] Application shutdown complete.")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
print("[init] FastAPI application instance created.")

# Middleware order matters - request ID should be first to be available in all other middleware
app.add_middleware(RequestIdMiddleware)
print("[init] RequestIdMiddleware registered.")

# Configure global request rate limiting.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
print("[init] Rate limiter and exception handler configured.")

# Configure CORS to allow frontend and other approved origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print(f"[init] CORS configured for origins: {settings.cors_origins_list}")

# Register API route groups.
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
    print("[request] GET / called")
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Basic health check endpoint."""
    print("[request] GET /health called")
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies database and Redis connectivity.

    Returns 503 if any dependency is unavailable.
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal
    print("[request] GET /health/ready called")

    checks = {"database": "unknown", "redis": "unknown"}
    print("[health/ready] Running dependency readiness checks...")

    # Check database
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
        print("[health/ready] Database check passed.")
    except Exception as exc:
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"[health/ready] Database check failed: {exc}")

    # Check Redis
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
    print(f"[health/ready] Final checks: {checks}")

    if not all_healthy:
        print("[health/ready] Dependencies unhealthy, returning 503.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    print("[health/ready] All dependencies healthy.")
    return {"status": "healthy", "checks": checks}
