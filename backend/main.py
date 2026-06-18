# StockSageAI - Backend server entry point
"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

# ---------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------
import os  # Operating system interface for path and environment operations
import asyncio  # Async I/O framework for concurrent operations
import sys  # System-specific parameters and functions
from contextlib import asynccontextmanager  # Provides utilities for managing async setup/teardown

# Windows compatibility: Use Selector event loop instead of the default ProactorEventLoop
# The ProactorEventLoop on Windows can have compatibility issues with:
#   - SSL/TLS connections
#   - Subprocess handling
#   - Third-party async libraries
# SelectorEventLoop is more compatible and stable for general use.
if sys.platform == "win32":
    # This avoids occasional async/socket edge cases seen on Windows with Proactor.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("🪟 Windows selector event loop policy enabled")

# ---------------------------------------------------------------
# Module-level setup
# Executed once when the Python module is first imported by uvicorn.
# ---------------------------------------------------------------

# ---------------------------------------------------------------
# Third-party imports
# ---------------------------------------------------------------
from fastapi import FastAPI  # Web framework for building APIs
from fastapi.middleware.cors import CORSMiddleware  # Cross-Origin Resource Sharing middleware
from slowapi import _rate_limit_exceeded_handler  # Rate limit error handler
from slowapi.errors import RateLimitExceeded  # Exception raised when rate limit is hit
from sqlalchemy import text  # Raw SQL execution helper

# ---------------------------------------------------------------
# Application imports - API route routers for different features
# Each router maps to a distinct API path (e.g. /auth/*, /stocks/*)
# Modular router structure allows independent development and testing.
# Routers are included in app registration section below.
# ---------------------------------------------------------------
from api import alerts as alerts_router  # Stock price alert management - price thresholds
from api import auth as auth_router  # User authentication and authorization - JWT tokens
from api import chat as chat_router  # AI-powered chat/assistant feature - LLM integration
from api import news as news_router  # Financial news endpoints - news aggregation
from api import prices as prices_router  # Stock price data endpoints - OHLCV data
from api import report as report_router  # Single stock report generation - detailed analysis
from api import reports as reports_router  # Batch reports endpoint - multiple stocks
from api import sentiment as sentiment_router  # Market sentiment analysis - social media
from api import stocks as stocks_router  # Stock search and discovery - search/filter
from api import watchlist as watchlist_router  # User watchlist management - tracked stocks

# ---------------------------------------------------------------
# Application imports - core configuration and utilities
# ---------------------------------------------------------------
from core.config import get_settings  # Application settings from environment
from core.limiter import limiter  # Rate limiting instance
from core.logging import get_logger, setup_logging  # Structured logging setup
from core.middleware import RequestIdMiddleware  # Adds unique request IDs to each request

# ---------------------------------------------------------------
# Application imports - database and data layer
# ---------------------------------------------------------------
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


# ---------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------

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
        print("🧱 Ensuring pgcrypto extension is available")
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        print("🗂️ Creating/updating database tables from ORM metadata")
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
    print("🌍 Closing scraper browser pool")
    await close_browser_pool()
    print("✅ Cleanup complete")


# ---------------------------------------------------------------
# FastAPI app instantiation
# ---------------------------------------------------------------

# Create the FastAPI application instance
app = FastAPI(
    title=settings.app_name,  # Application name from configuration
    version="0.1.0",           # Current API version
    lifespan=lifespan,         # Startup/shutdown lifecycle handler
)

# This print confirms module-level startup execution when the app process boots.
print("Server is starting...")
print("📦 FastAPI app object created")

# ---------------------------------------------------------------
# Middleware stack
# Middleware is applied in the order it is added (first = outermost).
# ---------------------------------------------------------------

# Middleware order matters - request ID should be first to be available in all other middleware
app.add_middleware(RequestIdMiddleware)
print("Request ID middleware added")

# Set up rate limiting with SlowAPI
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
print("Rate limiter configured")

# CORS (Cross-Origin Resource Sharing) configuration
# Controls which external domains can make requests to this API.
# Security note: allow_methods=["*"] permits all HTTP verbs (GET, POST, DELETE, etc.)
#               If restricting to specific methods, list them explicitly: ["GET", "POST"]
# Production recommendation: Keep origins list minimal, enumerate specific frontend URLs.
# Current config pulls origins from environment variable (see core/config.py).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,  # List of allowed frontend domains
    allow_credentials=True,  # Allow cookies and credentials in cross-origin requests
    allow_methods=["*"],     # Allow all HTTP methods
    allow_headers=["*"],     # Allow all headers (auth tokens, content-type, etc.)
)
print("CORS middleware configured")

# ---------------------------------------------------------------
# Route registration and API endpoint setup
# Each router is mounted with its feature-specific prefix (e.g., /auth, /stocks).
# Order of registration does not affect routing priority (FastAPI handles this).
# All registered routers are available immediately after app startup.
# ---------------------------------------------------------------

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
print("🧭 API route map finalized")
print("✅ Application bootstrapping complete")

# ---------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------


@app.get("/")
async def root():
    """Root endpoint returning app info.
    
    Useful as a quick sanity check - returns app metadata without hitting any dependencies.
    """
    # Useful as a quick sanity endpoint for reverse proxy and app wiring checks.
    print("Root endpoint hit")
    print(f"↩️ Returning root payload for {settings.app_name}")
    # Return basic application metadata
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    """Basic liveness check endpoint.
    
    Returns 200 if the application is running (doesn't verify dependencies).
    Used by Docker/Kubernetes liveness probes.
    """
    print("Health check (liveness) requested")
    print("✅ Liveness check passed")
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies database and Redis connectivity.

    Returns 200 if all dependencies are healthy, 503 if any are unavailable.
    This endpoint is typically used by container orchestration systems (Docker, Kubernetes)
    to determine if the service is ready to handle traffic.
    
    Difference from /health:
    - /health: Just checks if app process is running (liveness)
    - /health/ready: Checks if external dependencies are reachable (readiness)
    """
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    # Track dependency health in a single response object for clear diagnostics.
    # Orchestrators examine this object to make routing decisions.
    checks = {"database": "unknown", "redis": "unknown"}
    # Readiness probes are usually called by orchestrators before routing traffic.
    print("🔎 Readiness check requested")

    # Check database connectivity
    # Tests the primary data store where all user data is persisted.
    # SELECT 1 is a lightweight query that confirms the connection works.
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))  # Minimal query to test connection
        checks["database"] = "healthy"
        print("✅ Database check: OK")
    except Exception as exc:
        # Database down means we cannot serve any requests that read/write data
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"❌ Database check failed: {exc}")

    # Check Redis connectivity (used for caching and rate limiting)
    # Redis outage doesn't break core functionality but degrades performance.
    try:
        redis = cache_service.get_redis()
        # PING command is the standard Redis healthcheck
        await redis.ping()
        checks["redis"] = "healthy"
        print("✅ Redis check: OK")
    except Exception as exc:
        # Redis down means cache layer is unavailable, but app can still function
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        print(f"❌ Redis check failed: {exc}")

    # Aggregate results - must have all dependencies healthy
    all_healthy = all(v == "healthy" for v in checks.values())
    # Keep this summary log for quick triage during startup incidents.
    print(f"📋 Readiness summary: {checks}")

    # Return 503 (Service Unavailable) if any critical dependency is down
    # 503 tells load balancers to remove this instance from the rotation
    if not all_healthy:
        print(f"⚠️ Readiness check failed: {checks}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    # 200 OK - all systems operational, safe to route traffic here
    print("💚 All systems ready - server can accept traffic")
    return {"status": "healthy", "checks": checks}
