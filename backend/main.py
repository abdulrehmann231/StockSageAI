from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"app": settings.app_name, "version": "0.1.0", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
