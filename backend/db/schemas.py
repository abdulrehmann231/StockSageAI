import uuid
from datetime import datetime
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

    price: float
    previous_close: float | None = None
    open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: int | None = None

    week_52_high: float | None = None
    week_52_low: float | None = None

    market_cap: float | None = None
    pe_ratio: float | None = None
    eps: float | None = None
    dividend_yield: float | None = None  # percent, e.g. 2.5 == 2.5%

    change: float | None = None
    change_pct: float | None = None

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
