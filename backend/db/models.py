import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.config import get_settings
from db.session import Base

_settings = get_settings()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    default_market: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_profile: Mapped[str | None] = mapped_column(String, nullable=True)
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "default_market IS NULL OR default_market IN ('PSX', 'GLOBAL', 'BOTH')",
            name="ck_users_default_market",
        ),
    )


class Stock(Base):
    __tablename__ = "stocks"

    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    market: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sector: Mapped[str | None] = mapped_column(String, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FilingChunk(Base):
    """A single embedded chunk of a financial filing (Filings RAG agent, plan § 4.6).

    Each row is one ~1000-token slice of a SEC 10-K/10-Q (global) or a PSX annual
    report. The ``embedding`` column is a pgvector column; retrieval pre-filters on
    the plain SQL metadata columns (``ticker``, ``market``, ``filing_type``,
    ``fiscal_year``) BEFORE/while ranking by cosine distance — that pre-filtering is
    exactly why we use pgvector over a hosted vector DB with limited metadata filters.
    """

    __tablename__ = "filing_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # --- Pre-filter metadata (real, indexable SQL columns) ---
    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String, nullable=False)  # 'PSX' | 'GLOBAL'
    filing_type: Mapped[str] = mapped_column(String, nullable=False)  # '10-K' | '10-Q' | 'annual_report'
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. 'MD&A', 'Risk Factors'

    # --- Source / citation metadata ---
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- Payload ---
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(_settings.embedding_dim), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Composite B-tree so per-ticker pre-filtering is cheap before the vector scan.
        Index("ix_filing_chunks_ticker_type_year", "ticker", "filing_type", "fiscal_year"),
        CheckConstraint(
            "market IN ('PSX', 'GLOBAL')",
            name="ck_filing_chunks_market",
        ),
    )
