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


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
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
