# StockSageAI - Backend server entry point
"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

import os  # Operating system interface for path and environment operations
import asyncio  # Async I/O framework for concurrent operations
import sys  # System-specific parameters and functions
from contextlib import asynccontextmanager  # Provides utilities for managing async setup/teardown

# Windows compatibility: Use Selector event loop instead of the default ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Third-party imports
from fastapi import FastAPI  # Web framework for building APIs
from fastapi.middleware.cors import CORSMiddleware  # Cross-Origin Resource Sharing middleware
from slowapi import _rate_limit_exceeded_handler  # Rate limit error handler
from slowapi.errors import RateLimitExceeded  # Exception raised when rate limit is hit
from sqlalchemy import text  # Raw SQL execution helper

# API route routers for different features
from api import alerts as alerts_router  # Stock price alert management
from api import auth as auth_router  # User authentication and authorization
from api import chat as chat_router  # AI-powered chat/assistant feature
from api import news as news_router  # Financial news endpoints
from api import prices as prices_router  # Stock price data endpoints
from api import report as report_router  # Single stock report generation
from api import reports as reports_router  # Batch reports endpoint
from api import sentiment as sentiment_router  # Market sentiment analysis
from api import stocks as stocks_router  # Stock search and discovery
from api import watchlist as watchlist_router  # User watchlist management

# Core configuration and utilities
from core.config import get_settings  # Application settings from environment
from core.limiter import limiter  # Rate limiting instance
from core.logging import get_logger, setup_logging  # Structured logging setup
from core.middleware import RequestIdMiddleware  # Adds unique request IDs to each request

# Database setup
from db.session import Base, engine  # SQLAlchemy ORM base and engine
from services import cache_service  # Redis-based caching service

# Initialize logging before anything else
setup_logging()
print("📝 Logging system initialized")

# Load application configuration from environment variables
settings = get_settings()
print(f"⚙️  Configuration loaded: {settings.app_name}")

logger = get_logger(__name__)

print(f"🚀 Starting {settings.app_name} v0.1.0")
print(f"🌐 Running on platform: {sys.platform}")
print(f"🔌 Allowed CORS origins: {settings.cors_origins_list}")


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
app.include_router(auth_router.router)     # /auth/* - login, signup, tokens
print("  ✅ Auth router registered")
app.include_router(stocks_router.router)   # /stocks/* - search and stock info
print("  ✅ Stocks router registered")
app.include_router(prices_router.router)   # /prices/* - historical and real-time prices
print("  ✅ Prices router registered")
app.include_router(sentiment_router.router) # /sentiment/* - market sentiment
print("  ✅ Sentiment router registered")
app.include_router(news_router.router)     # /news/* - financial news
print("  ✅ News router registered")
app.include_router(report_router.router)   # /report/* - stock report
print("  ✅ Report router registered")
app.include_router(reports_router.router)  # /reports/* - batch reports
print("  ✅ Reports router registered")
app.include_router(chat_router.router)     # /chat/* - AI assistant
print("  ✅ Chat router registered")
app.include_router(watchlist_router.router) # /watchlist/* - user watchlists
print("  ✅ Watchlist router registered")
app.include_router(alerts_router.router)   # /alerts/* - price alerts
print("  ✅ Alerts router registered")
print("All API routers registered")
print("✅ Application bootstrapping complete")


@app.get("/")
async def root():
    """Root endpoint returning app info.
    
    Useful as a quick sanity check - returns app metadata without hitting any dependencies.
    """
    # Useful as a quick sanity endpoint for reverse proxy and app wiring checks.
    print("Root endpoint hit")
    # Return basic application metadata
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Basic liveness check endpoint.
    
    Returns 200 if the application is running (doesn't verify dependencies).
    Used by Docker/Kubernetes liveness probes.
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

    # Aggregate results - must have all dependencies healthy
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

    print("💚 All systems ready - server can accept traffic")
    return {"status": "healthy", "checks": checks}
