from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "StockSage AI"
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # Database
    database_url: str

    # Redis
    redis_url: str

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080

    # CORS
    cors_origins: str = "http://localhost:3000"

    # LLM providers (all optional at scaffold stage)
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None

    # Pinecone
    pinecone_api_key: str | None = None
    pinecone_index_name: str = "stocksage"

    # Observability
    sentry_dsn: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
