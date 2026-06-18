"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
Handles initialization, middleware configuration, and graceful shutdown.
"""

import os
import sys
import asyncio
from contextlib import asynccontextmanager

print("[INIT] StockSageAI Backend Starting...")
print(f"[INIT] Running on platform: {sys.platform}")

# Windows requires Selector event loop instead of ProactorEventLoop
# to avoid compatibility issues with SSL, subprocess, and third-party async libraries
# This is critical for Windows environments - without it, async operations may hang
if sys.platform == "win32":
    print("[INIT] Detected Windows platform - configuring async event loop...")
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("[INIT] ✓ Windows selector event loop policy enabled")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

# ============================================================================
# API ROUTE IMPORTS - All endpoint modules
# ============================================================================
from api import (
    alerts as alerts_router,     # Real-time price alerts
    auth as auth_router,         # Authentication & JWT
    chat as chat_router,         # Chat interface for queries
    news as news_router,         # News aggregation endpoints
    prices as prices_router,     # Market price data
    report as report_router,     # Single report generation
    reports as reports_router,   # Report management
    sentiment as sentiment_router,  # Social sentiment analysis
    stocks as stocks_router,     # Stock metadata & search
    watchlist as watchlist_router,  # User watchlist management
)

# ============================================================================
# CORE CONFIGURATION & SERVICES
# ============================================================================
from core.config import get_settings
from core.limiter import limiter              # Rate limiting
from core.logging import get_logger, setup_logging  # Structured logging
from core.middleware import RequestIdMiddleware     # Request tracking
from db.session import Base, engine          # Database ORM & engine
from services import cache_service           # Redis caching

# Initialize logging and config before anything else - this is critical!
print("[INIT] Setting up logging and configuration...")
setup_logging()
settings = get_settings()
logger = get_logger(__name__)
print(f"[INIT] ✓ Configuration loaded. App: {settings.app_name}")


# ============================================================================
# APPLICATION LIFESPAN - Startup & Shutdown Handlers
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Startup Phase:
    - Creates PostgreSQL extensions (pgcrypto for UUID generation)
    - Initializes database schema from ORM models
    - Sets up all background services

    Shutdown Phase:
    - Closes Redis/cache connections gracefully
    - Closes database connection pool
    - Closes browser pool used by scrapers
    - Logs shutdown event
    """
    logger.info("Starting application", extra={"app_name": settings.app_name})
    print("\n" + "="*60)
    print("[STARTUP] 🚀 Application starting...")
    print("="*60)

    # Ensure pgcrypto extension exists - needed for UUID generation in PostgreSQL
    print("[STARTUP] Initializing PostgreSQL extensions...")
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        # Create all ORM-mapped tables if they don't exist
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized")
    print("[STARTUP] ✓ Database schema ready")
    print("[STARTUP] ✓ Application fully initialized and ready to serve requests\n")
    yield

    # Cleanup resources on shutdown - perform graceful shutdown
    print("\n" + "="*60)
    print("[SHUTDOWN] 🛑 Application shutting down gracefully...")
    print("="*60)
    logger.info("Shutting down application")
    print("[SHUTDOWN] Closing cache connections...")
    await cache_service.close()
    print("[SHUTDOWN] ✓ Cache closed")
    
    print("[SHUTDOWN] Disposing database connections...")
    await engine.dispose()
    print("[SHUTDOWN] ✓ Database disposed")

    print("[SHUTDOWN] Closing browser pool...")
    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()
    print("[SHUTDOWN] ✓ Browser pool closed")
    print("[SHUTDOWN] ✓ Cleanup complete\n")


# ============================================================================
# FASTAPI APPLICATION FACTORY
# ============================================================================
print("[INIT] Creating FastAPI application instance...")
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Multi-agent AI platform for stock research (PSX & Global)",
    lifespan=lifespan,
)
print(f"[INIT] ✓ FastAPI app created: {settings.app_name} v0.1.0")

# ============================================================================
# MIDDLEWARE CONFIGURATION - Order matters! Outermost runs first
# ============================================================================
print("[INIT] Configuring middleware stack...")

# Request ID middleware MUST be outermost - needs to wrap all other middleware
# Generates unique ID for each request for correlation across logs
print("[INIT]   - Adding RequestIdMiddleware (outermost)...")
app.add_middleware(RequestIdMiddleware)

# Rate limiting - prevents abuse by limiting requests per client
print("[INIT]   - Adding rate limiting...")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS (Cross-Origin Resource Sharing) — controls which external domains can reach the API
# Frontend at vercel app needs to call this backend, so we allow those origins
# Production recommendation: keep origins list minimal, enumerate specific frontend URLs
print(f"[INIT]   - Configuring CORS...")
print(f"[INIT]     Allowed origins: {settings.cors_origins_list}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,  # List of allowed frontend domains (e.g., vercel app URL)
    allow_credentials=True,  # Allow cookies and credentials in cross-origin requests
    allow_methods=["*"],     # Allow all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],     # Allow all headers (Authorization tokens, Content-Type, custom headers, etc.)
)
print("[INIT] ✓ Middleware configured")

# ============================================================================
# ROUTE REGISTRATION - All API endpoints attached here
# ============================================================================
print("[INIT] Registering API routers...")

router_list = [
    ("Auth", auth_router.router),
    ("Stocks", stocks_router.router),
    ("Prices", prices_router.router),
    ("Sentiment", sentiment_router.router),
    ("News", news_router.router),
    ("Report", report_router.router),
    ("Reports", reports_router.router),
    ("Chat", chat_router.router),
    ("Watchlist", watchlist_router.router),
    ("Alerts", alerts_router.router),
]

for name, router in router_list:
    app.include_router(router)
    print(f"[INIT]   ✓ {name} routes registered")

print(f"[INIT] ✓ All {len(router_list)} router modules registered")


# ============================================================================
# CORE HEALTH CHECK ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint returning app metadata and build information.
    
    Returns:
        dict: Application name, version, and status
    """
    print("[API] GET / - Root endpoint called")
    return {
        "app": settings.app_name,
        "version": "0.1.0",
        "status": "ok",
        "environment": settings.environment,
    }


@app.get("/health")
async def health():
    """Liveness check - returns 200 if the process is running.
    
    This is a simple heartbeat check used by container orchestrators (Docker, K8s)
    to verify the process hasn't crashed. It does NOT check external dependencies.
    
    Returns:
        dict: Status indicating process is alive
    """
    print("[HEALTH] GET /health - Liveness check")
    return {"status": "healthy", "check_type": "liveness"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check - verifies database and Redis connectivity.

    Returns 200 if all dependencies are healthy, 503 if any are unavailable.
    This is used by container orchestrators (Docker, Kubernetes) to decide
    whether to route traffic to this instance.
    
    Difference from /health:
    - /health:                   liveness — just checks the process is running
    - /health/ready:             readiness — checks external dependencies are working
    
    This endpoint is critical for:
    - Kubernetes readiness probes (stops routing traffic if fails)
    - Load balancer routing decisions
    - Graceful deployment/rollout handling
    
    Returns:
        dict: Status and individual component health checks
        
    Raises:
        HTTPException: 503 if any dependency is unhealthy
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    print("[HEALTH] GET /health/ready - Readiness check (checking dependencies)")
    
    # Track dependency health — orchestrators examine this to make routing decisions
    checks = {"database": "unknown", "redis": "unknown"}

    # ========== DATABASE CHECK ==========
    # Check database connectivity with a lightweight SELECT 1 query
    print("[HEALTH]   - Checking database connectivity...")
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))  # Minimal query to test connection
        checks["database"] = "healthy"
        print("[HEALTH]     ✓ Database: OK")
    except Exception as exc:
        # Database down means we cannot serve any requests that read/write data
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"[HEALTH]     ✗ Database: FAILED - {str(exc)[:50]}...")

    # ========== REDIS CHECK ==========
    # Check Redis connectivity — outage degrades performance but doesn't break core functionality
    print("[HEALTH]   - Checking Redis/cache connectivity...")
    try:
        redis = cache_service.get_redis()
        # PING command is the standard Redis healthcheck
        await redis.ping()
        checks["redis"] = "healthy"
        print("[HEALTH]     ✓ Redis: OK")
    except Exception as exc:
        # Redis down means cache layer is unavailable, but app can still function
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        print(f"[HEALTH]     ✗ Redis: FAILED - {str(exc)[:50]}...")

    all_healthy = all(v == "healthy" for v in checks.values())

    # Return 503 if any dependency is down — tells load balancers to stop routing traffic here
    if not all_healthy:
        print(f"[HEALTH] ✗ Readiness check FAILED - Some dependencies are unhealthy")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks, "message": "One or more dependencies are unavailable"},
        )

    # 200 OK — all systems operational, safe to route traffic here
    print(f"[HEALTH] ✓ Readiness check PASSED - All dependencies healthy")
    return {"status": "healthy", "check_type": "readiness", "checks": checks}
