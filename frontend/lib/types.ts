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

export type Verdict = "BUY" | "ACCUMULATE" | "HOLD" | "REDUCE" | "SELL";
export type ConfidenceLevel = "low" | "medium" | "high";
export type AlertType = "PRICE_DROP" | "PRICE_RISE" | "PRICE_TARGET" | "BIG_NEWS" | "SENTIMENT_SHIFT";

export interface ReportRecord {
  id: string;
  ticker: string;
  market: string;
  verdict: Verdict | null;
  confidence: ConfidenceLevel | null;
  composite_score: number | null;
  created_at: string;
}

export interface ReportDetail extends ReportRecord {
  report_data: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface ChatTurn {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}

export interface WatchlistItem {
  ticker: string;
  name: string;
  market: string;
  sector: string | null;
  currency: string | null;
  added_at: string;
}

export interface Alert {
  id: string;
  ticker: string;
  alert_type: AlertType;
  condition: Record<string, unknown>;
  is_active: boolean;
  cooldown_hours: number;
  last_triggered: string | null;
  created_at: string;
}

export interface StockReport {
  ticker: string;
  market: string;
  company_name: string | null;
  verdict: Verdict;
  confidence: ConfidenceLevel;
  composite_score: number;
  executive_summary: string;
  price_summary: string | null;
  news_summary: string | null;
  sentiment_summary: string | null;
  key_catalysts: string[];
  risks: string[];
  opportunities: string[];
  price: PriceQuote | null;
  news: unknown;
  sentiment: unknown;
  sources: string[];
  errors: string[];
  model_used: string | null;
  fetched_at: string;
  cached: boolean;
}
