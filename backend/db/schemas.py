import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

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


# ---------- Filings RAG (plan § 4.6) ----------


class FilingCitation(BaseModel):
    """A single source chunk backing an answer — lets the UI link to evidence."""

    filing_type: str
    fiscal_year: int | None = None
    section: str | None = None
    page: int | None = None
    source_url: str | None = None
    snippet: str  # short excerpt of the chunk content
    distance: float | None = None  # cosine distance; lower = more relevant


class FilingAnswer(BaseModel):
    """A grounded answer to one auto-generated filings question."""

    question: str
    answer: str
    citations: list[FilingCitation] = Field(default_factory=list)
    grounded: bool = True  # False when no relevant chunks were found (answer is a stub)


class FilingsResult(BaseModel):
    """Structured ``filings_data`` returned by the Filings RAG agent."""

    ticker: str
    market: str
    company_name: str | None = None
    answers: list[FilingAnswer] = Field(default_factory=list)
    chunks_indexed: int = 0  # how many filing chunks exist for this ticker
    fetched_at: datetime
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
