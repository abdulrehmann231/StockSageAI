import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Market = Literal["PSX", "GLOBAL", "BOTH"]
RiskProfile = Literal["Conservative", "Moderate", "Aggressive"]


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None
    default_market: Market | None = None
    risk_profile: RiskProfile | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    default_market: str | None
    risk_profile: str | None
    email_notifications: bool
    created_at: datetime


class AuthResponse(BaseModel):
    """Body returned alongside the httpOnly auth cookie set on signup/login."""

    user: UserOut


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    name: str
    market: str
    sector: str | None
    industry: str | None
    market_cap: Decimal | None
    currency: str | None
    is_active: bool


class PriceQuote(BaseModel):
    """Snapshot of live/last-known pricing data for a single ticker."""

    ticker: str
    market: str
    currency: str | None = None

    # Core price data
    price: float
    previous_close: float | None = None
    open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: int | None = None

    # Range data
    week_52_high: float | None = None
    week_52_low: float | None = None

    # Valuation metrics
    market_cap: float | None = None
    pe_ratio: float | None = None
    eps: float | None = None
    dividend_yield: float | None = None  # percent, e.g. 2.5 == 2.5%

    # Change data
    change: float | None = None
    change_pct: float | None = None

    # Additional PSX data (may be None for global stocks)
    total_shares: int | None = None
    free_float_shares: int | None = None
    free_float_pct: float | None = None
    net_profit_margin: float | None = None

    # Listing status — for PSX, comes from the "DELISTED" badge and the
    # "As of <date>" stamp on dps.psx.com.pk. Global path always sets the
    # defaults (False / None) since yfinance doesn't surface delisting.
    is_delisted: bool = False
    data_as_of: date | None = None

    # Metadata
    fetched_at: datetime
    source: str  # "yfinance" | "psx"
    cached: bool = False


# ---------- Pagination ----------


class PaginationMeta(BaseModel):
    """Metadata for paginated responses."""

    total: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_prev: bool


class PaginatedStocks(BaseModel):
    """Paginated list of stocks."""

    items: list[StockOut]
    meta: PaginationMeta


# --------------------------------------------------------------------------- #
# Phase 6 — Watchlist
# --------------------------------------------------------------------------- #


class WatchlistAddRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)


class WatchlistItemOut(BaseModel):
    """Watchlist row with enough stock context for a mini-card."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str
    name: str
    market: str
    sector: str | None = None
    currency: str | None = None
    added_at: datetime


# --------------------------------------------------------------------------- #
# Phase 6 — Reports persistence
# --------------------------------------------------------------------------- #


class ReportGenerateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    refresh: bool = False
    max_news_articles: int = Field(default=5, ge=1, le=20)


class ReportRecordOut(BaseModel):
    """Lightweight Report row for list views (no full report_data blob)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    market: str
    verdict: str | None = None
    confidence: str | None = None
    composite_score: float | None = None
    created_at: datetime


class ReportDetailOut(ReportRecordOut):
    """Full record including the persisted ``StockReport`` payload."""

    report_data: dict[str, Any]


# --------------------------------------------------------------------------- #
# Phase 6 — Chat
# --------------------------------------------------------------------------- #


class ChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ChatTurnOut(BaseModel):
    """The two messages produced by a single ``POST /chat/{id}/message`` call."""

    user_message: ChatMessageOut
    assistant_message: ChatMessageOut


# --------------------------------------------------------------------------- #
# Phase 6 — Alerts
# --------------------------------------------------------------------------- #


AlertType = Literal[
    "PRICE_DROP",
    "PRICE_RISE",
    "PRICE_TARGET",
    "BIG_NEWS",
    "SENTIMENT_SHIFT",
]


class AlertCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    alert_type: AlertType
    condition: dict[str, Any]
    cooldown_hours: int = Field(default=24, ge=0, le=24 * 14)


class AlertUpdateRequest(BaseModel):
    is_active: bool | None = None
    condition: dict[str, Any] | None = None
    cooldown_hours: int | None = Field(default=None, ge=0, le=24 * 14)


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    alert_type: AlertType
    condition: dict[str, Any]
    is_active: bool
    cooldown_hours: int
    last_triggered: datetime | None = None
    created_at: datetime


class AlertFiredEvent(BaseModel):
    """Result of a single alert evaluation that fired (used by the engine)."""

    alert_id: uuid.UUID
    user_id: uuid.UUID
    ticker: str
    alert_type: AlertType
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    fired_at: datetime


class AlertEngineRunResult(BaseModel):
    """Summary of one engine sweep."""

    scanned: int
    fired: list[AlertFiredEvent]
    errors: list[str] = Field(default_factory=list)
    skipped_cooldown: int = 0


# --------------------------------------------------------------------------- #
# Phase 7 — Portfolio tracker (plan § 4.15)
# --------------------------------------------------------------------------- #


TransactionType = Literal["BUY", "SELL"]


class HoldingCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    quantity: float = Field(gt=0)
    avg_buy_price: float = Field(gt=0)
    buy_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class HoldingUpdateRequest(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    avg_buy_price: float | None = Field(default=None, gt=0)
    buy_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class HoldingOut(BaseModel):
    """A holding enriched with live P&L (computed by the portfolio service)."""

    id: uuid.UUID
    ticker: str
    name: str | None = None
    market: str
    sector: str | None = None
    currency: str | None = None
    quantity: float
    avg_buy_price: float
    buy_date: date | None = None
    notes: str | None = None
    is_active: bool

    # Live P&L — None when the price could not be fetched.
    current_price: float | None = None
    current_value: float | None = None
    cost_basis: float = 0.0
    gain_loss: float | None = None
    gain_loss_pct: float | None = None
    is_delisted: bool = False
    price_error: str | None = None


class PortfolioMetrics(BaseModel):
    """Aggregate, portfolio-wide numbers."""

    total_value: float = 0.0
    total_cost_basis: float = 0.0
    total_gain_loss: float = 0.0
    total_gain_loss_pct: float = 0.0
    day_change: float = 0.0
    holdings_count: int = 0
    priced_count: int = 0
    best_performer: dict[str, Any] | None = None
    worst_performer: dict[str, Any] | None = None
    sector_allocation: dict[str, float] = Field(default_factory=dict)
    market_allocation: dict[str, float] = Field(default_factory=dict)


class PortfolioOut(BaseModel):
    """Full portfolio: enriched holdings + aggregate metrics."""

    holdings: list[HoldingOut] = Field(default_factory=list)
    metrics: PortfolioMetrics = Field(default_factory=PortfolioMetrics)
    errors: list[str] = Field(default_factory=list)
    fetched_at: datetime


class TransactionCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    transaction_type: TransactionType
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    transaction_date: date
    fees: float = Field(default=0, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    transaction_type: TransactionType
    quantity: float
    price: float
    transaction_date: date
    fees: float
    notes: str | None = None
    created_at: datetime


class TaxLotEstimate(BaseModel):
    holding_id: uuid.UUID
    ticker: str
    market: str
    quantity: float
    cost_basis: float
    current_value: float | None = None
    gain_loss: float | None = None
    holding_period_days: int | None = None
    is_long_term: bool | None = None
    tax_rate_pct: float | None = None
    estimated_tax: float | None = None
    near_long_term_threshold: bool = False
    note: str | None = None


class TaxEstimateOut(BaseModel):
    """Estimated capital-gains tax liability if everything were sold today."""

    lots: list[TaxLotEstimate] = Field(default_factory=list)
    total_estimated_tax: float = 0.0
    total_gain_loss: float = 0.0
    currency_note: str | None = None
    fetched_at: datetime


class PerformancePoint(BaseModel):
    snapshot_date: date
    total_value: float
    total_cost_basis: float
    total_gain_loss: float


class PerformanceOut(BaseModel):
    range: str
    points: list[PerformancePoint] = Field(default_factory=list)


class PortfolioAnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    health_score: int
    analysis_data: dict[str, Any]
    recommendations: dict[str, Any] | None = None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Phase 4 — Filings RAG (plan § 4.6)
# --------------------------------------------------------------------------- #


class FilingsIndexRequest(BaseModel):
    limit: int = Field(default=1, ge=1, le=5)


class FilingsAskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    k: int = Field(default=5, ge=1, le=15)


class FilingCitation(BaseModel):
    citation: str
    filing_type: str | None = None
    fiscal_year: int | None = None
    section: str | None = None
    page: int | None = None
    url: str | None = None
    similarity: float
    excerpt: str


class FilingsAnswer(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    ticker: str
    question: str
    answer: str
    citations: list[FilingCitation] = Field(default_factory=list)
    grounded: bool = False
    model_used: str | None = None
    fetched_at: datetime


class FilingsData(BaseModel):
    """Compiled answers to the five auto-generated key questions."""

    ticker: str
    market: str
    indexed: bool
    filing_count: int
    chunk_count: int
    answers: dict[str, FilingsAnswer] = Field(default_factory=dict)
    fetched_at: datetime
