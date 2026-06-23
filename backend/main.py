"""FastAPI application entry point.

Sets up the application with middleware, routes, and database lifecycle.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

print("[sys] Checking platform for event loop policy...")
if sys.platform == "win32":
    print("[sys] Windows detected — applying SelectorEventLoopPolicy.")
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("[sys] WindowsSelectorEventLoopPolicy applied.")
else:
    print(f"[sys] Non-Windows platform ({sys.platform}) — no event loop policy change needed.")
print("[sys] Platform check complete.")

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

print("[init] ========================================")
print("[init] Starting StockTrust Backend Bootstrap...")
print("[init] ========================================")

print("[init] Step 1/5: Configuring logging subsystem...")
setup_logging()
print("[init] Logging configured successfully.")
print("[init] Step 1/5 complete.")

print("[init] Step 2/5: Loading application settings...")
settings = get_settings()
print(f"[init] Settings loaded. App name: '{settings.app_name}', Version: 0.1.0")
print(f"[init] CORS origins: {settings.cors_origins_list}")

logger = get_logger(__name__)
print(f"[init] Loaded settings for app: {settings.app_name}")
print("[init] Step 2/5 complete.")


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

    print("[startup] ========================================")
    print(f"[startup] Booting {settings.app_name} v0.1.0...")
    print("[startup] ========================================")
    logger.info("Starting application", extra={"app_name": settings.app_name, "phase": "startup"})

    print("[startup] --- Step 1/3: Database initialization ---")
    print("[startup] Ensuring required DB extension (pgcrypto) and schema exist...")

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

    print("[startup] --- Step 2/3: Cache service ---")
    print("[startup] Cache service will initialize on first use (lazy).")
    print("[startup] --- Step 2/3 complete ---")

    print("[startup] --- Step 3/3: Finalizing ---")
    print(f"[startup] {settings.app_name} is ready to accept requests.")
    logger.info("Application startup complete",
                extra={"app_name": settings.app_name, "version": "0.1.0", "phase": "startup"})
    print("[startup] ========================================")
    print("[startup] Startup sequence finished successfully.")
    print("[startup] ========================================")

    yield

    print("[shutdown] ========================================")
    print("[shutdown] Shutdown sequence started.")
    print("[shutdown] ========================================")
    logger.info("Shutting down application", extra={"phase": "shutdown"})

    print("[shutdown] --- Step 1/3: Closing cache service ---")
    print("[shutdown]   -> Closing Redis cache connections...")
    await cache_service.close()
    print("[shutdown]   -> Cache service closed.")
    logger.info("Cache service shut down", extra={"phase": "shutdown", "component": "cache"})
    print("[shutdown] --- Step 1/3 complete ---")

    print("[shutdown] --- Step 2/3: Disposing database engine ---")
    print("[shutdown]   -> Disposing SQLAlchemy database engine...")
    await engine.dispose()
    print("[shutdown]   -> Database engine disposed.")
    logger.info("Database engine shut down", extra={"phase": "shutdown", "component": "database"})
    print("[shutdown] --- Step 2/3 complete ---")

    print("[shutdown] --- Step 3/3: Closing browser pool ---")
    from scrapers.browser_pool import close_browser_pool
    print("[shutdown]   -> Closing headless browser pool (if active)...")
    await close_browser_pool()
    print("[shutdown]   -> Browser pool closed.")
    logger.info("Browser pool shut down", extra={"phase": "shutdown", "component": "browser_pool"})
    print("[shutdown] --- Step 3/3 complete ---")

    print("[shutdown] All resources released successfully.")
    print("[shutdown] Application shutdown complete.")
    logger.info("Application shutdown complete", extra={"phase": "shutdown"})
    print("[shutdown] ========================================")
    print("[shutdown] Goodbye.")
    print("[shutdown] ========================================")


print("[init] Step 3/5: Creating FastAPI application instance...")
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
print("[init] FastAPI application instance created.")
logger.info("FastAPI app instance created", extra={"title": settings.app_name, "version": "0.1.0"})
print("[init] Step 3/5 complete.")

print("[init] Step 4/5: Configuring middleware...")

print("[init]   -> Registering RequestIdMiddleware...")
app.add_middleware(RequestIdMiddleware)
print("[init]   -> RequestIdMiddleware registered.")
logger.info("RequestIdMiddleware installed", extra={"phase": "init", "component": "middleware"})

print("[init]   -> Attaching rate limiter to app state...")
app.state.limiter = limiter
print("[init]   -> Registering RateLimitExceeded exception handler...")
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
print("[init]   -> Rate limiter and exception handler configured.")
logger.info("Rate limiter installed", extra={"phase": "init", "component": "rate_limiter"})

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

print("[init] ========================================")
print("[init] Bootstrap complete! Application is fully configured.")
print("[init] ========================================")
print("[init] Waiting for ASGI server to start serving traffic...")


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
    print("[request] GET / called — returning root metadata.")
    logger.info("Root endpoint hit", extra={"method": "GET", "path": "/"})

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
    print("[request] GET /health called — liveness check.")
    logger.info("Health endpoint hit", extra={"method": "GET", "path": "/health"})

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

    checks = {"database": "unknown", "redis": "unknown"}

    print("[health/ready] Starting dependency readiness checks...")
    print("[health/ready] Target dependencies: PostgreSQL, Redis")

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

    all_healthy = all(v == "healthy" for v in checks.values())
    print(f"[health/ready] ========================================")
    print(f"[health/ready] Readiness check results: {checks}")
    print(f"[health/ready] Overall status: {'HEALTHY' if all_healthy else 'UNHEALTHY'}")
    print(f"[health/ready] ========================================")
    logger.info("Readiness check complete", extra={"checks": checks, "all_healthy": all_healthy})

    if not all_healthy:
        print("[health/ready] One or more dependencies unhealthy — returning HTTP 503.")
        logger.warning("Readiness check failed, returning 503", extra={"checks": checks})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    print("[health/ready] All dependencies healthy — returning HTTP 200.")
    logger.info("Readiness check passed, returning 200", extra={"checks": checks})
    return {"status": "healthy", "checks": checks}
