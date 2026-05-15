export type Market = "PSX" | "GLOBAL" | "BOTH";
export type RiskProfile = "Conservative" | "Moderate" | "Aggressive";

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  default_market: string | null;
  risk_profile: string | null;
  email_notifications: boolean;
  created_at: string;
}

export interface AuthResponse {
  user: User;
}

export interface Stock {
  ticker: string;
  name: string;
  market: string;
  sector: string | null;
  industry: string | null;
  market_cap: string | null;
  currency: string | null;
  is_active: boolean;
}

export interface PriceQuote {
  ticker: string;
  market: string;
  currency: string | null;

  price: number;
  previous_close: number | null;
  open: number | null;
  day_high: number | null;
  day_low: number | null;
  volume: number | null;

  week_52_high: number | null;
  week_52_low: number | null;

  market_cap: number | null;
  pe_ratio: number | null;
  eps: number | null;
  dividend_yield: number | null;

  change: number | null;
  change_pct: number | null;

  fetched_at: string;
  source: string;
  cached: boolean;
}
