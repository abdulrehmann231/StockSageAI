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
export type NewsImpact =
  | "HIGH_POSITIVE"
  | "MEDIUM_POSITIVE"
  | "NEUTRAL"
  | "MEDIUM_NEGATIVE"
  | "HIGH_NEGATIVE";

export interface NewsArticle {
  ticker: string;
  market: string;
  title: string;
  url: string;
  source: string;
  published_at: string | null;
  summary: string;
  impact: NewsImpact;
  catalysts: string[];
  relevance_score: number;
}

export interface NewsResult {
  ticker: string;
  market: string;
  company_name: string | null;
  overall_news_sentiment: NewsImpact;
  top_catalyst: string | null;
  articles: NewsArticle[];
  fetched_at: string;
  sources: string[];
  errors: string[];
  cached: boolean;
}

export interface SentimentResult {
  ticker: string;
  market: string;
  company_name: string | null;
  overall_sentiment: number;
  label: string;
  bullish_pct: number;
  bearish_pct: number;
  top_bullish_points: string[];
  top_bearish_points: string[];
  post_count: number;
  sources: string[];
  errors: string[];
  fetched_at: string;
  cached: boolean;
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
  news: NewsResult | null;
  sentiment: SentimentResult | null;
  sources: string[];
  errors: string[];
  model_used: string | null;
  fetched_at: string;
  cached: boolean;
}

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
  report_data: StockReport;
}

// --------------------------------------------------------------------------- //
// Phase 6 — Watchlist, Alerts, Chat
// --------------------------------------------------------------------------- //

export interface WatchlistItem {
  ticker: string;
  name: string;
  market: string;
  sector: string | null;
  currency: string | null;
  added_at: string;
}

export type AlertType =
  | "PRICE_DROP"
  | "PRICE_RISE"
  | "PRICE_TARGET"
  | "BIG_NEWS"
  | "SENTIMENT_SHIFT";

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

export interface AlertCreateRequest {
  ticker: string;
  alert_type: AlertType;
  condition: Record<string, unknown>;
  cooldown_hours?: number;
}

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  created_at: string;
}

export interface ChatTurn {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}
