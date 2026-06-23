"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import asyncio   # Provides async I/O primitives and the event loop
import sys       # Used for platform detection and system-level operations
from contextlib import asynccontextmanager  # Enables async context manager syntax for lifespan

# ---------------------------------------------------------------------------
# Windows event loop compatibility fix
# ---------------------------------------------------------------------------
# On Windows, the default ProactorEventLoop can cause issues with certain
# async libraries (e.g., SQLAlchemy async engine). Switching to the
# SelectorEventLoop resolves most of these compatibility problems.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ---------------------------------------------------------------------------
# Third-party imports
# ---------------------------------------------------------------------------
from fastapi import FastAPI                          # Core web framework
from fastapi.middleware.cors import CORSMiddleware   # Handles cross-origin requests
from slowapi import _rate_limit_exceeded_handler     # Custom 429 response handler
from slowapi.errors import RateLimitExceeded         # Exception raised on rate limit breach
from sqlalchemy import text                          # Allows raw SQL execution via SQLAlchemy

# ---------------------------------------------------------------------------
# Internal API router imports
# ---------------------------------------------------------------------------
# Each module below encapsulates a distinct domain of the API surface.
# Importing them here keeps the composition root clean and explicit.
from api import alerts as alerts_router      # Manages user price/event alerts
from api import auth as auth_router          # Handles login, registration, and token management
from api import chat as chat_router          # AI-powered chat and assistant features
from api import news as news_router          # Aggregates and serves financial news
from api import prices as prices_router      # Provides real-time and historical price data
from api import report as report_router      # Generates individual stock reports
from api import reports as reports_router    # Handles batch and historical report queries
from api import sentiment as sentiment_router  # Analyzes and returns market sentiment scores
from api import stocks as stocks_router      # Stock search, metadata, and lookup
from api import watchlist as watchlist_router  # User watchlist CRUD operations

# ---------------------------------------------------------------------------
# Core application module imports
# ---------------------------------------------------------------------------
from core.config import get_settings         # Loads config from env vars / .env file
from core.limiter import limiter             # Shared rate limiter instance (SlowAPI)
from core.logging import get_logger, setup_logging  # Structured logging setup
from core.middleware import RequestIdMiddleware      # Attaches unique request IDs to each request
from db.session import Base, engine          # SQLAlchemy declarative base and async engine
from services import cache_service           # Redis-backed cache service abstraction

# ---------------------------------------------------------------------------
# Logging initialization
# ---------------------------------------------------------------------------
# Must be called before any logger is created so all log output is
# formatted and routed correctly from the very start of the process.
setup_logging()

# ---------------------------------------------------------------------------
# Application settings
# ---------------------------------------------------------------------------
# Reads configuration from environment variables (and .env files).
# Contains values like app name, CORS origins, DB URL, Redis URL, etc.
settings = get_settings()

# Module-level logger — used for startup, shutdown, and top-level events.
logger = get_logger(__name__)


# ===========================================================================
# APPLICATION LIFESPAN
# ===========================================================================
# The lifespan context manager replaces the older @app.on_event("startup")
# and @app.on_event("shutdown") pattern. It runs setup code before the first
# request and teardown code after the last request (or on SIGTERM/SIGINT).
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database schema on startup and cleans up connections on shutdown.

    Startup sequence:
        1. Log the boot event for operator visibility.
        2. Ensure the pgcrypto PostgreSQL extension exists.
        3. Create/update all database tables from SQLAlchemy ORM metadata.

    Shutdown sequence:
        1. Close the Redis cache connection pool.
        2. Dispose of the SQLAlchemy database engine (closes all DB connections).
        3. Close the headless browser pool used by scrapers (if active).
    """

    # --- STARTUP ---
    logger.info("Starting application", extra={"app_name": settings.app_name, "phase": "startup"})

    # Open a transactional connection to the database for schema setup.
    # CREATE EXTENSION IF NOT EXISTS is idempotent — safe to run every boot.
    # Base.metadata.create_all synchronizes ORM model definitions to the DB.
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))  # Required for UUID generation
        await conn.run_sync(Base.metadata.create_all)  # Create tables if they don't exist

    logger.info("Database initialized and schema up-to-date",
                extra={"phase": "startup", "component": "database"})

    logger.info("Application startup complete",
                extra={"app_name": settings.app_name, "version": "0.1.0", "phase": "startup"})

    # Yield control to FastAPI — the app now starts serving HTTP requests.
    # Code below the yield runs only after the server begins shutting down.
    yield

    # --- SHUTDOWN ---
    logger.info("Shutting down application", extra={"phase": "shutdown"})

    # Close the Redis connection pool gracefully.
    # This ensures in-flight cache operations are completed before the process exits.
    await cache_service.close()
    logger.info("Cache service shut down", extra={"phase": "shutdown", "component": "cache"})

    # Dispose of the SQLAlchemy engine, closing all pooled DB connections.
    # This prevents connection leaks on restart or process termination.
    await engine.dispose()
    logger.info("Database engine shut down", extra={"phase": "shutdown", "component": "database"})

    # Deferred import: not all deployments use the browser scraping feature.
    # close_browser_pool() is a no-op if the pool was never initialized.
    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()
    logger.info("Browser pool shut down", extra={"phase": "shutdown", "component": "browser_pool"})

    logger.info("Application shutdown complete", extra={"phase": "shutdown"})


# ===========================================================================
# FASTAPI APPLICATION INSTANCE
# ===========================================================================
# The app object is the ASGI application. It is created once at import time
# and passed to the ASGI server (uvicorn). All middleware and routers are
# attached to this object before any requests are served.
app = FastAPI(
    title=settings.app_name,   # Shown in the auto-generated OpenAPI docs
    version="0.1.0",           # API version surfaced in /openapi.json
    lifespan=lifespan,         # Wires up the startup/shutdown handler above
)
logger.info("FastAPI app instance created", extra={"title": settings.app_name, "version": "0.1.0"})

# ===========================================================================
# MIDDLEWARE STACK
# ===========================================================================
# Middleware is applied in reverse registration order on the response path.
# Registration order (request path): RequestId → RateLimit → CORS → handler

# Attach a unique request ID to every incoming request.
# Downstream handlers and logs can reference this ID for tracing.
app.add_middleware(RequestIdMiddleware)
logger.info("RequestIdMiddleware installed", extra={"phase": "init", "component": "middleware"})

# Attach the SlowAPI rate limiter to app state so route decorators can use it.
# The exception handler converts rate limit violations into proper HTTP 429 responses.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
logger.info("Rate limiter installed", extra={"phase": "init", "component": "rate_limiter"})

# Configure CORS so browser clients from allowed origins can access the API.
# allow_credentials=True is required to support cookie-based auth flows.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,  # Loaded from config (env-specific)
    allow_credentials=True,                    # Required for auth cookies / headers
    allow_methods=["*"],                       # Allow all HTTP methods
    allow_headers=["*"],                       # Allow all request headers
)
logger.info("CORS middleware installed", extra={"phase": "init", "component": "cors", "origins": settings.cors_origins_list})

# ===========================================================================
# ROUTER REGISTRATION
# ===========================================================================
# Each router adds a group of related endpoints under its own prefix.
# Registering all routers here creates a single, clear composition root.
app.include_router(auth_router.router)       # /auth/*
app.include_router(stocks_router.router)     # /stocks/*
app.include_router(prices_router.router)     # /prices/*
app.include_router(sentiment_router.router)  # /sentiment/*
app.include_router(news_router.router)       # /news/*
app.include_router(report_router.router)     # /report/*
app.include_router(reports_router.router)    # /reports/*
app.include_router(chat_router.router)       # /chat/*
app.include_router(watchlist_router.router)  # /watchlist/*
app.include_router(alerts_router.router)     # /alerts/*
logger.info("All API routers registered", extra={"phase": "init", "router_count": 10})


# ===========================================================================
# BUILT-IN HTTP ENDPOINTS
# ===========================================================================

@app.get("/")
async def root():
    """Root endpoint returning app info.

    This is the most basic endpoint in the application. It returns the app
    name, version, and a status field. Useful for:
      - Quick smoke tests after deployment
      - API metadata discovery
      - Verifying the app is reachable and responding
    """
    logger.info("Root endpoint hit", extra={"method": "GET", "path": "/"})

    # Build and return a minimal metadata payload.
    response_data = {"app": settings.app_name, "version": "0.1.0", "status": "ok"}

    logger.info("Root response sent", extra={"path": "/", "status_code": 200})
    return response_data


@app.get("/health")
async def health():
    """Basic health check endpoint.

    Liveness endpoint: confirms the service process is running and the ASGI
    server is accepting connections. This does NOT check external dependencies
    (database, Redis, etc.) — that is the responsibility of /health/ready.

    Kubernetes and other orchestrators use this to decide if the pod should
    be restarted. If this endpoint fails, the process is considered dead.
    """
    logger.info("Health endpoint hit", extra={"method": "GET", "path": "/health"})

    # A 200 response here means the process is alive and serving requests.
    logger.info("Health response sent", extra={"path": "/health", "status_code": 200})
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check that verifies database and Redis connectivity.

    Returns 503 if any dependency is unavailable.

    While /health confirms the process is alive, /health/ready confirms the
    application can actually serve requests. It probes:
      1. PostgreSQL — via a lightweight SELECT 1 query
      2. Redis — via a PING command

    Orchestrators use this to control traffic routing: a pod that fails
    readiness is removed from the load balancer until it recovers.
    """
    # Deferred imports — only needed inside this endpoint.
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    logger.info("Readiness endpoint hit", extra={"method": "GET", "path": "/health/ready"})

    # Track per-dependency health status. Values: "healthy" | "unhealthy" | "unknown".
    checks = {"database": "unknown", "redis": "unknown"}

    # --- PostgreSQL probe ---
    # Run the simplest possible query to confirm DB connectivity and query execution.
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))  # Lightweight connectivity check
        checks["database"] = "healthy"
        logger.info("Database readiness check passed", extra={"component": "database", "result": "healthy"})
    except Exception as exc:
        # Log the raw exception so engineers can diagnose the root cause.
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        logger.error("Database readiness check failed", extra={"component": "database", "result": "unhealthy", "error": str(exc)})

    # --- Redis probe ---
    # Send a PING command — the fastest way to confirm Redis is reachable.
    try:
        redis = cache_service.get_redis()
        await redis.ping()  # Raises if Redis is unreachable or misconfigured
        checks["redis"] = "healthy"
        logger.info("Redis readiness check passed", extra={"component": "redis", "result": "healthy"})
    except Exception as exc:
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        logger.error("Redis readiness check failed", extra={"component": "redis", "result": "unhealthy", "error": str(exc)})

    # Aggregate: all dependencies must be healthy for the pod to be considered ready.
    all_healthy = all(v == "healthy" for v in checks.values())
    logger.info("Readiness check complete", extra={"checks": checks, "all_healthy": all_healthy})

    if not all_healthy:
        # Return HTTP 503 so the load balancer stops routing traffic here.
        # This is a temporary state — the next probe will re-evaluate readiness.
        logger.warning("Readiness check failed, returning 503", extra={"checks": checks})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    # All dependencies healthy — return 200 to signal the pod is ready for traffic.
    logger.info("Readiness check passed, returning 200", extra={"checks": checks})
    return {"status": "healthy", "checks": checks}
