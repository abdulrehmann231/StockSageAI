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
from core.logging import setup_logging
from core.middleware import RequestIdMiddleware
from db.session import Base, engine
from services import cache_service

setup_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.run_sync(Base.metadata.create_all)

    yield

    await cache_service.close()

    await engine.dispose()

    from scrapers.browser_pool import close_browser_pool
    await close_browser_pool()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RequestIdMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/")
async def root():
    response_data = {"app": settings.app_name, "version": "0.1.0", "status": "ok"}
    return response_data


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    from fastapi import HTTPException, status
    from db.session import SessionLocal

    checks = {"database": "unknown", "redis": "unknown"}

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception:
        checks["database"] = "unhealthy"

    try:
        redis = cache_service.get_redis()
        await redis.ping()
        checks["redis"] = "healthy"
    except Exception:
        checks["redis"] = "unhealthy"

    all_healthy = all(v == "healthy" for v in checks.values())

    if not all_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "checks": checks},
        )

    return {"status": "healthy", "checks": checks}
