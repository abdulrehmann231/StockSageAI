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
