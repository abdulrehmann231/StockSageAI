# =============================================================================
# StockTrust Backend - FastAPI Application Entry Point
# =============================================================================
# This module is the root of the StockTrust backend service. It bootstraps the
# FastAPI application, wires up middleware, registers all API route handlers,
# and manages the application lifecycle (startup/shutdown).
#
# Execution starts here when the ASGI server (uvicorn) loads this file.
# =============================================================================

"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

# ---------------------------------------------------------------------------
# Python Standard Library Imports
# ---------------------------------------------------------------------------
# These are built-in modules that come with Python's standard library.
# They provide foundational async, OS, and system-level utilities.
import asyncio          # Async I/O framework for managing event loops
import sys              # System-specific parameters and functions
from contextlib import asynccontextmanager  # Decorator for async context managers

# ---------------------------------------------------------------------------
# Windows Event Loop Configuration
# ---------------------------------------------------------------------------
# On Windows, the default proactor event loop policy can cause issues with
# subprocess and socket operations in some async libraries. The selector
# policy is more broadly compatible and avoids subtle runtime errors.
# This check runs once at module import time, before any async code runs.
print("[sys] Checking platform for event loop policy...")
if sys.platform == "win32":
    # Windows-specific event loop note:
    # The selector policy tends to be more compatible with libraries that rely on
    # socket-based async I/O. Setting it early avoids subtle runtime issues that
    # can appear on Windows with the default proactor policy in some environments.
    print("[sys] Windows detected — applying SelectorEventLoopPolicy.")
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("[sys] WindowsSelectorEventLoopPolicy applied.")
else:
    print(f"[sys] Non-Windows platform ({sys.platform}) — no event loop policy change needed.")
print("[sys] Platform check complete.")

# ---------------------------------------------------------------------------
# Third-Party Library Imports
# ---------------------------------------------------------------------------
# FastAPI itself — the web framework powering this application.
from fastapi import FastAPI
# CORS middleware for cross-origin request handling.
from fastapi.middleware.cors import CORSMiddleware
# SlowAPI provides rate-limiting support via decorators.
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
# SQLAlchemy text() for raw SQL queries within the ORM framework.
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Application Module Imports
# ---------------------------------------------------------------------------
# Each router module encapsulates a distinct domain area of the API.
# They are imported here and registered on the app below.
from api import alerts as alerts_router      # Alert management endpoints
from api import auth as auth_router            # Authentication and authorization
from api import chat as chat_router            # AI chat / assistant endpoints
from api import news as news_router            # Financial news aggregation
from api import prices as prices_router        # Stock price data
from api import report as report_router        # Single report generation
from api import reports as reports_router      # Batch/historical reports
from api import sentiment as sentiment_router  # Market sentiment analysis
from api import stocks as stocks_router        # Stock lookup and metadata
from api import watchlist as watchlist_router  # User watchlist management

# ---------------------------------------------------------------------------
# Core / Internal Imports
# ---------------------------------------------------------------------------
# Configuration loader (reads from environment variables / .env files).
from core.config import get_settings
# Rate limiter singleton instance.
from core.limiter import limiter
# Structured logging setup and module-level logger factory.
from core.logging import get_logger, setup_logging
# Custom middleware for attaching unique request IDs to each HTTP request.
from core.middleware import RequestIdMiddleware
# SQLAlchemy Base (declarative base) and async engine for DB access.
from db.session import Base, engine
# Application-level cache service abstraction (backed by Redis).
from services import cache_service

# ===========================================================================
# APPLICATION BOOTSTRAP
# ===========================================================================
# Everything above this line is import / config wiring.
# Everything below runs sequentially at import time to set up the application
# before the ASGI server starts serving traffic.

print("[init] ========================================")
print("[init] Starting StockTrust Backend Bootstrap...")
print("[init] ========================================")

# ---------------------------------------------------------------------------
# Logging Initialization
# ---------------------------------------------------------------------------
# Logging must be configured first so all subsequent bootstrap steps can use
# the structured logger instead of bare print() calls.
print("[init] Step 1/5: Configuring logging subsystem...")
setup_logging()
print("[init] Logging configured successfully.")
print("[init] Step 1/5 complete.")

# ---------------------------------------------------------------------------
# Settings & Logger
# ---------------------------------------------------------------------------
# Load application settings from environment variables. This reads from
# .env files and OS env vars to determine the runtime configuration.
print("[init] Step 2/5: Loading application settings...")
settings = get_settings()
print(f"[init] Settings loaded. App name: '{settings.app_name}', Version: 0.1.0")
print(f"[init] CORS origins: {settings.cors_origins_list}")

# Create a module-level logger that will be used throughout this file.
logger = get_logger(__name__)
# Print once at import time so startup configuration is visible in local runs.
print(f"[init] Loaded settings for app: {settings.app_name}")
print("[init] Step 2/5 complete.")


# ===========================================================================
# APPLICATION LIFESPAN HANDLER
# ===========================================================================
# The lifespan context manager is a FastAPI feature that runs startup logic
# before the first request and shutdown logic after the last request.
# It replaces the older startup/shutdown event pattern and is more robust
# because exceptions during startup prevent the app from serving traffic.

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
    
    # =========================================================================
    # STARTUP PHASE
    # =========================================================================
    # This code runs when the ASGI server starts. It prepares external
    # dependencies (database, cache) before the first HTTP request arrives.
    
    print("[startup] ========================================")
    print(f"[startup] Booting {settings.app_name} v0.1.0...")
    print("[startup] ========================================")
    logger.info("Starting application", extra={"app_name": settings.app_name, "phase": "startup"})
    
    print("[startup] --- Step 1/3: Database initialization ---")
    print("[startup] Ensuring required DB extension (pgcrypto) and schema exist...")
    
    # -----------------------------------------------------------------------
    # Database Schema Setup
    # -----------------------------------------------------------------------
    # The `pgcrypto` extension provides cryptographic functions used by the
    # application (e.g., gen_random_uuid()). We use CREATE EXTENSION IF NOT EXISTS
    # so it's safe to run on every startup — no-op if already present.
    # After the extension, we run Base.metadata.create_all to synchronize the
    # database schema with the current SQLAlchemy ORM model definitions.
    # -----------------------------------------------------------------------
    async with engine.begin() as conn:
        print("[startup]   -> Creating pgcrypto extension (if not exists)...")
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        print("[startup]   -> pgcrypto extension ready.")
        
        print("[startup]   -> Creating/updating database tables from models...")
        await conn.run_sync(Base.metadata.create_all)
        print("[startup]   -> Database schema synchronized.")
    
    print("[startup] Database initialization completed.")
    logger.info("Database initialized and schema up-to-date",
                extra={"phase": "startup", "component": "database"})
    print("[startup] --- Step 1/3 complete ---")
    
    # -----------------------------------------------------------------------
    # Cache / Redis Verification (optional)
    # -----------------------------------------------------------------------
    # The cache service (Redis) will be initialized lazily on first use.
    # We could add a proactive ping here, but that is done in the /health/ready
    # endpoint instead so failures during startup don't block the process.
    # -----------------------------------------------------------------------
    print("[startup] --- Step 2/3: Cache service ---")
    print("[startup] Cache service will initialize on first use (lazy).")
    print("[startup] --- Step 2/3 complete ---")
    
    # -----------------------------------------------------------------------
    # Final Startup Confirmation
    # -----------------------------------------------------------------------
    print("[startup] --- Step 3/3: Finalizing ---")
    print(f"[startup] {settings.app_name} is ready to accept requests.")
    logger.info("Application startup complete",
                extra={"app_name": settings.app_name, "version": "0.1.0", "phase": "startup"})
    print("[startup] ========================================")
    print("[startup] Startup sequence finished successfully.")
    print("[startup] ========================================")
    
    # Yield control back to FastAPI — the application now serves requests.
    # Everything after this yield runs during shutdown.
    yield
    
    # =========================================================================
    # SHUTDOWN PHASE
    # =========================================================================
    # This code runs when the ASGI server receives a shutdown signal (SIGTERM,
    # SIGINT, or when the context manager exits). It releases external resources
    # in a predictable order to minimize disruption to in-flight operations.
    
    print("[shutdown] ========================================")
    print("[shutdown] Shutdown sequence started.")
    print("[shutdown] ========================================")
    logger.info("Shutting down application", extra={"phase": "shutdown"})
    
    print("[shutdown] --- Step 1/3: Closing cache service ---")
    # -----------------------------------------------------------------------
    # Redis Cache Cleanup
    # -----------------------------------------------------------------------
    # Close the Redis connection pool so connections are returned gracefully.
    # The cache_service.close() method is idempotent — safe to call even if
    # Redis was never initialized.
    # -----------------------------------------------------------------------
    print("[shutdown]   -> Closing Redis cache connections...")
    await cache_service.close()
    print("[shutdown]   -> Cache service closed.")
    logger.info("Cache service shut down", extra={"phase": "shutdown", "component": "cache"})
    print("[shutdown] --- Step 1/3 complete ---")
    
    print("[shutdown] --- Step 2/3: Disposing database engine ---")
    # -----------------------------------------------------------------------
    # Database Engine Cleanup
    # -----------------------------------------------------------------------
    # Dispose of the SQLAlchemy async engine. This closes all database
    # connections in the connection pool and releases any associated resources.
    # After disposal, the engine cannot be used again.
    # -----------------------------------------------------------------------
    print("[shutdown]   -> Disposing SQLAlchemy database engine...")
    await engine.dispose()
    print("[shutdown]   -> Database engine disposed.")
    logger.info("Database engine shut down", extra={"phase": "shutdown", "component": "database"})
    print("[shutdown] --- Step 2/3 complete ---")
    
    print("[shutdown] --- Step 3/3: Closing browser pool ---")
    # -----------------------------------------------------------------------
    # Headless Browser Cleanup
    # -----------------------------------------------------------------------
    # The scraper system uses a pool of headless Chromium browsers (via
    # Playwright) to fetch dynamic web content. The import is deferred here
    # because not all deployments use the scraping feature, and the browser
    # pool dependency (playwright) may not be installed in every environment.
    # close_browser_pool() is a no-op if the pool was never initialized.
    # -----------------------------------------------------------------------
    from scrapers.browser_pool import close_browser_pool
    print("[shutdown]   -> Closing headless browser pool (if active)...")
    await close_browser_pool()
    print("[shutdown]   -> Browser pool closed.")
    logger.info("Browser pool shut down", extra={"phase": "shutdown", "component": "browser_pool"})
    print("[shutdown] --- Step 3/3 complete ---")
    
    # -----------------------------------------------------------------------
    # Final Shutdown Confirmation
    # -----------------------------------------------------------------------
    print("[shutdown] All resources released successfully.")
    # All resources released, shutdown is complete.
    print("[shutdown] Application shutdown complete.")
    logger.info("Application shutdown complete", extra={"phase": "shutdown"})
    print("[shutdown] ========================================")
    print("[shutdown] Goodbye.")
    print("[shutdown] ========================================")


# ===========================================================================
# FASTAPI APPLICATION INSTANCE
# ===========================================================================
# App object creation happens at import, before serving traffic.
# This is the central FastAPI application that will be served by the ASGI
# server (uvicorn). All configuration (title, version, lifespan) is set here.
print("[init] Step 3/5: Creating FastAPI application instance...")
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
# App object creation happens at import, before serving traffic.
print("[init] FastAPI application instance created.")
logger.info("FastAPI app instance created", extra={"title": settings.app_name, "version": "0.1.0"})
print("[init] Step 3/5 complete.")

# ===========================================================================
# MIDDLEWARE CONFIGURATION
# ===========================================================================
# Middleware is software that runs on every request/response cycle. The order
# matters: middleware added first runs first on the request path (outermost),
# and last on the response path (innermost). We configure:
#   1. RequestIdMiddleware — adds a unique correlation ID to each request
#   2. Rate limiter — protects endpoints from abuse
#   3. CORS — controls cross-origin resource sharing
print("[init] Step 4/5: Configuring middleware...")

# ---------------------------------------------------------------------------
# Request ID Middleware
# ---------------------------------------------------------------------------
# This middleware runs first so downstream middleware and route handlers
# can attach logs/traces to a stable request correlation ID. This improves
# debuggability across distributed components, especially when requests span
# multiple services.
# Middleware ordering note:
# Request ID middleware runs first so downstream middleware and route handlers
# can attach logs/traces to a stable request correlation ID. This improves
# debuggability across distributed components.
print("[init]   -> Registering RequestIdMiddleware...")
app.add_middleware(RequestIdMiddleware)
print("[init]   -> RequestIdMiddleware registered.")
logger.info("RequestIdMiddleware installed", extra={"phase": "init", "component": "middleware"})

# ---------------------------------------------------------------------------
# Rate Limiting Setup
# ---------------------------------------------------------------------------
# SlowAPI provides decorator-based rate limiting. The limiter singleton is
# attached to app.state so route handlers can reference it. We also register
# a custom exception handler that returns a consistent 429 Too Many Requests
# response instead of a generic 500 error when the rate limit is exceeded.
# Rate-limiting setup:
# The limiter is attached to app state for use by route decorators, and a
# dedicated exception handler translates quota violations into consistent HTTP
# responses instead of generic server errors.
print("[init]   -> Attaching rate limiter to app state...")
app.state.limiter = limiter
print("[init]   -> Registering RateLimitExceeded exception handler...")
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
print("[init]   -> Rate limiter and exception handler configured.")
logger.info("Rate limiter installed", extra={"phase": "init", "component": "rate_limiter"})

# ---------------------------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------------------------
# Cross-Origin Resource Sharing (CORS) controls which web origins are allowed
# to make browser-based requests to this API. Allowed origins come from
# configuration so environments can differ safely (local, staging, production).
# Credentials are enabled to support auth flows where cookies or authenticated
# browser requests (e.g., Authorization headers) are required.
# CORS policy note:
# Allowed origins come from configuration so environments can differ safely
# (local, staging, production). Credentials are enabled to support auth flows
# where cookies or authenticated browser requests are required.
print("[init]   -> Configuring CORS middleware...")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print(f"[init]   -> CORS configured for origins: {settings.cors_origins_list}")
logger.info("CORS middleware installed", extra={"phase": "init", "component": "cors", "origins": settings.cors_origins_list})

print("[init] Step 4/5 complete.")

# ===========================================================================
# ROUTER REGISTRATION
# ===========================================================================
# Each router encapsulates a domain area (auth, stocks, prices, sentiment, etc.)
# so route files stay focused and maintainable. Registering them here creates a
# single, clear composition root for the full HTTP surface of the backend.
# The order of registration determines URL matching priority — more specific
# routes should generally be registered first, though FastAPI handles this
# well with its OpenAPI-based routing.
print("[init] Step 5/5: Registering API routers...")
print("[init]   -> Registering auth routes...")
app.include_router(auth_router.router)
print("[init]   -> Registering stocks routes...")
app.include_router(stocks_router.router)
print("[init]   -> Registering prices routes...")
app.include_router(prices_router.router)
print("[init]   -> Registering sentiment routes...")
app.include_router(sentiment_router.router)
print("[init]   -> Registering news routes...")
app.include_router(news_router.router)
print("[init]   -> Registering report routes...")
app.include_router(report_router.router)
print("[init]   -> Registering reports routes...")
app.include_router(reports_router.router)
print("[init]   -> Registering chat routes...")
app.include_router(chat_router.router)
print("[init]   -> Registering watchlist routes...")
app.include_router(watchlist_router.router)
print("[init]   -> Registering alerts routes...")
app.include_router(alerts_router.router)
print("[init] All 10 API routers registered successfully.")
logger.info("All API routers registered", extra={"phase": "init", "router_count": 10})
print("[init] Step 5/5 complete.")

# ===========================================================================
# BOOTSTRAP COMPLETE
# ===========================================================================
print("[init] ========================================")
print("[init] Bootstrap complete! Application is fully configured.")
print("[init] ========================================")
print("[init] Waiting for ASGI server to start serving traffic...")


# ===========================================================================
# HTTP API ENDPOINTS
# ===========================================================================
# All public HTTP routes are defined below. Each endpoint is a coroutine
# (async def) that FastAPI dispatches to when a matching HTTP request arrives.
# Logging at entry and exit points provides traceability for operations teams.
#
# Endpoint inventory:
#   GET  /              — Root metadata and smoke test
#   GET  /health        — Liveness probe (process alive check)
#   GET  /health/ready  — Readiness probe (dependency health check)
#
# Additional routes are registered via routers above (auth, stocks, etc.).
# ===========================================================================

print("[routes] Registering built-in endpoint: GET /")

@app.get("/")
async def root():
    """Root endpoint returning app info.
    
    This is the most basic endpoint in the application. It returns the app
    name, version, and a status field. Useful for:
      - Quick smoke tests after deployment
      - API metadata discovery
      - Verifying the app is reachable and responding
    """
    # Lightweight endpoint useful for quick smoke checks.
    # Log and print on every call so operators can see traffic in real-time.
    print("[request] GET / called — returning root metadata.")
    logger.info("Root endpoint hit", extra={"method": "GET", "path": "/"})
    
    # Build response payload with application metadata.
    # The status field is always "ok" — if we get here, the app is alive.
    response_data = {"app": settings.app_name, "version": "0.1.0", "status": "ok"}
    
    print("[request] GET / responding with app metadata.")
    logger.info("Root response sent", extra={"path": "/", "status_code": 200})
    return response_data


print("[routes] Registering built-in endpoint: GET /health")

@app.get("/health")
async def health():
    """Basic health check endpoint.
    
    Liveness endpoint: confirms the service process is running and the ASGI
    server is accepting connections. This does NOT check external dependencies
    (database, Redis, etc.) — that is the responsibility of /health/ready.
    
    Kubernetes and other orchestrators use this to decide if the pod should
    be restarted. If this endpoint fails, the process is considered dead.
    """
    # Liveness endpoint: service process is running.
    print("[request] GET /health called — liveness check.")
    logger.info("Health endpoint hit", extra={"method": "GET", "path": "/health"})
    
    # Simple response — if the server is serving, we are alive.
    print("[request] GET /health responding healthy.")
    logger.info("Health response sent", extra={"path": "/health", "status_code": 200})
    return {"status": "healthy"}


print("[routes] Registering built-in endpoint: GET /health/ready")

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
    from fastapi import HTTPException, status
    from db.session import SessionLocal
    
    print("[request] GET /health/ready called — readiness check.")
    logger.info("Readiness endpoint hit", extra={"method": "GET", "path": "/health/ready"})
    
    # -----------------------------------------------------------------------
    # Readiness semantics:
    # This endpoint checks external dependencies that are required for normal
    # request processing. If any check fails, we return 503 so orchestrators can
    # keep the instance out of rotation until dependencies recover.
    # -----------------------------------------------------------------------
    
    # Initialize check results dictionary. Each key maps to a dependency name
    # and the value will be one of: "healthy", "unhealthy", or "unknown".
    # Track dependency-specific status so callers can quickly identify which
    # component is degraded without parsing logs.
    checks = {"database": "unknown", "redis": "unknown"}
    
    print("[health/ready] Starting dependency readiness checks...")
    print("[health/ready] Target dependencies: PostgreSQL, Redis")
    
    # =========================================================================
    # DATABASE READINESS PROBE
    # =========================================================================
    # Database probe:
    # Perform a lightweight query through the normal async session path.
    # This validates connectivity plus basic query execution capability.
    # We use SELECT 1 — the simplest possible SQL query — to minimize load
    # while still confirming the database is reachable and responsive.
    print("[health/ready] --- Probing PostgreSQL ---")
    print("[health/ready] Executing SELECT 1 on PostgreSQL...")
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
        print("[health/ready] PostgreSQL probe SUCCESS — database is reachable.")
        logger.info("Database readiness check passed", extra={"component": "database", "result": "healthy"})
    except Exception as exc:
        logger.error("Database health check failed", extra={"error": str(exc)})
        checks["database"] = "unhealthy"
        print(f"[health/ready] PostgreSQL probe FAILED — {exc}")
        logger.error("Database readiness check failed", extra={"component": "database", "result": "unhealthy", "error": str(exc)})
    
    # =========================================================================
    # REDIS READINESS PROBE
    # =========================================================================
    # Redis probe:
    # Use ping as a minimal command to validate that the cache service is
    # reachable and responsive. The cache_service.get_redis() call returns
    # the Redis client (or raises if not configured), and redis.ping()
    # confirms the connection is alive.
    print("[health/ready] --- Probing Redis ---")
    print("[health/ready] Sending PING to Redis...")
    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
        print("[health/ready] Redis probe SUCCESS — cache is reachable.")
        logger.info("Redis readiness check passed", extra={"component": "redis", "result": "healthy"})
    except Exception as exc:
        logger.error("Redis health check failed", extra={"error": str(exc)})
        checks["redis"] = "unhealthy"
        print(f"[health/ready] Redis probe FAILED — {exc}")
        logger.error("Redis readiness check failed", extra={"component": "redis", "result": "unhealthy", "error": str(exc)})
    
    # =========================================================================
    # RESULTS AGGREGATION
    # =========================================================================
    all_healthy = all(v == "healthy" for v in checks.values())
    # Emit a single summary line to simplify troubleshooting from container logs.
    print(f"[health/ready] ========================================")
    print(f"[health/ready] Readiness check results: {checks}")
    print(f"[health/ready] Overall status: {'HEALTHY' if all_healthy else 'UNHEALTHY'}")
    print(f"[health/ready] ========================================")
    logger.info("Readiness check complete", extra={"checks": checks, "all_healthy": all_healthy})
    
    if not all_healthy:
        # 503 indicates temporary unavailability due to dependency health, not
        # an application crash. This helps upstream systems make better decisions.
        # Orchestrators will stop routing traffic to this instance until the
        # next readiness probe succeeds.
        print("[health/ready] One or more dependencies unhealthy — returning HTTP 503.")
        logger.warning("Readiness check failed, returning 503", extra={"checks": checks})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )
    
    print("[health/ready] All dependencies healthy — returning HTTP 200.")
    logger.info("Readiness check passed, returning 200", extra={"checks": checks})
    return {"status": "healthy", "checks": checks}
