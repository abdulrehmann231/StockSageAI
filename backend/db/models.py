import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.session import Base


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


# --------------------------------------------------------------------------- #
# Phase 6 — chat / watchlist / alerts (plan § 6)
# --------------------------------------------------------------------------- #


class Report(Base):
    """A persisted ``StockReport`` produced by the Phase-5 orchestrator.

    Reports are anchored to a user so the chat endpoint can scope its history
    safely. ``report_data`` is the full ``StockReport.model_dump`` payload —
    storing it lets us answer chat follow-ups without re-running the agent
    fan-out for every question.
    """

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(
        String,
        ForeignKey("stocks.ticker", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    market: Mapped[str] = mapped_column(String, nullable=False)
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Numeric(asdecimal=False), nullable=True)
    report_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ChatMessage(Base):
    """A single user/assistant message in a chat thread anchored to a report."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_chat_messages_role",
        ),
    )


class WatchlistItem(Base):
    """Composite-PK row: ``(user_id, ticker)`` — a stock a user is watching."""

    __tablename__ = "watchlist"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ticker: Mapped[str] = mapped_column(
        String,
        ForeignKey("stocks.ticker", ondelete="CASCADE"),
        primary_key=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Alert(Base):
    """A user-owned trigger that fires when its condition is satisfied.

    ``alert_type`` chooses the evaluator (see ``workers/alert_engine.py``);
    ``condition`` is a JSONB blob whose shape varies per type, e.g.
    ``{"threshold_pct": -5.0}`` for ``PRICE_DROP`` or
    ``{"target": 200.0, "direction": "above"}`` for ``PRICE_TARGET``.
    ``cooldown_hours`` keeps a noisy alert from spamming the user.
    """

    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(
        String,
        ForeignKey("stocks.ticker", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alert_type: Mapped[str] = mapped_column(String, nullable=False)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_triggered: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cooldown_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "alert_type IN ('PRICE_DROP', 'PRICE_RISE', 'PRICE_TARGET', "
            "'BIG_NEWS', 'SENTIMENT_SHIFT')",
            name="ck_alerts_type",
        ),
    )


# --------------------------------------------------------------------------- #
# Phase 7 — portfolio tracker (plan § 4.15)
# --------------------------------------------------------------------------- #


class Holding(Base):
    """A current position a user holds in a single ticker.

    Multiple lots of the same stock are allowed (bought ENGRO at 280, then at
    320 — both tracked as separate rows). ``avg_buy_price`` is the per-lot cost
    basis. Marking a holding sold flips ``is_active`` to ``False`` so it drops
    out of live P&L while remaining for tax/transaction history.
    """

    __tablename__ = "holdings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(
        String,
        ForeignKey("stocks.ticker", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    avg_buy_price: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    buy_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_holdings_quantity_positive"),
        CheckConstraint("avg_buy_price > 0", name="ck_holdings_price_positive"),
    )


class Transaction(Base):
    """An immutable buy/sell event. Adding a holding auto-logs a BUY."""

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    holding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("holdings.id", ondelete="SET NULL"),
        nullable=True,
    )
    ticker: Mapped[str] = mapped_column(
        String,
        ForeignKey("stocks.ticker", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    fees: Mapped[float] = mapped_column(Numeric(asdecimal=False), default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('BUY', 'SELL')",
            name="ck_transactions_type",
        ),
    )


class PortfolioSnapshot(Base):
    """Daily snapshot of a user's portfolio value — powers the perf chart."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    total_value: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    total_cost_basis: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    total_gain_loss: Mapped[float] = mapped_column(Numeric(asdecimal=False), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "snapshot_date", name="uq_snapshot_user_date"),
    )


class PortfolioAnalysis(Base):
    """A persisted AI rebalancing analysis produced by the Portfolio Analyst Agent."""

    __tablename__ = "portfolio_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    health_score: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recommendations: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "health_score BETWEEN 0 AND 100",
            name="ck_analyses_health_score",
        ),
    )
