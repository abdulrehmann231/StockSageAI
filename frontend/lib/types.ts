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

// --------------------------------------------------------------------------- //
// Watchlist
// --------------------------------------------------------------------------- //

export interface WatchlistItem {
  ticker: string;
  name: string;
  market: string;
  sector: string | null;
  currency: string | null;
  added_at: string;
}

// --------------------------------------------------------------------------- //
// Portfolio (Phase 7)
// --------------------------------------------------------------------------- //

export interface Holding {
  id: string;
  ticker: string;
  name: string | null;
  market: string;
  sector: string | null;
  currency: string | null;
  quantity: number;
  avg_buy_price: number;
  buy_date: string | null;
  notes: string | null;
  is_active: boolean;
  current_price: number | null;
  current_value: number | null;
  cost_basis: number;
  gain_loss: number | null;
  gain_loss_pct: number | null;
  is_delisted: boolean;
  price_error: string | null;
}

export interface PortfolioMetrics {
  total_value: number;
  total_cost_basis: number;
  total_gain_loss: number;
  total_gain_loss_pct: number;
  day_change: number;
  holdings_count: number;
  priced_count: number;
  best_performer: { ticker: string; name: string | null; gain_loss_pct: number | null; gain_loss: number | null } | null;
  worst_performer: { ticker: string; name: string | null; gain_loss_pct: number | null; gain_loss: number | null } | null;
  sector_allocation: Record<string, number>;
  market_allocation: Record<string, number>;
}

export interface Portfolio {
  holdings: Holding[];
  metrics: PortfolioMetrics;
  errors: string[];
  fetched_at: string;
}

export interface TaxLotEstimate {
  holding_id: string;
  ticker: string;
  market: string;
  quantity: number;
  cost_basis: number;
  current_value: number | null;
  gain_loss: number | null;
  holding_period_days: number | null;
  is_long_term: boolean | null;
  tax_rate_pct: number | null;
  estimated_tax: number | null;
  near_long_term_threshold: boolean;
  note: string | null;
}

export interface TaxEstimate {
  lots: TaxLotEstimate[];
  total_estimated_tax: number;
  total_gain_loss: number;
  currency_note: string | null;
  fetched_at: string;
}

export interface PerformancePoint {
  snapshot_date: string;
  total_value: number;
  total_cost_basis: number;
  total_gain_loss: number;
}

export interface Performance {
  range: string;
  points: PerformancePoint[];
}

export interface PortfolioAnalysis {
  id: string;
  health_score: number;
  analysis_data: {
    summary?: string;
    strengths?: string[];
    weaknesses?: string[];
    recommendations?: string[];
    tax_loss_opportunities?: string[];
    concentration_warnings?: string[];
    model_used?: string | null;
  };
  recommendations: { items?: string[] } | null;
  created_at: string;
}

export interface Transaction {
  id: string;
  ticker: string;
  transaction_type: "BUY" | "SELL";
  quantity: number;
  price: number;
  transaction_date: string;
  fees: number;
  notes: string | null;
  created_at: string;
}

// --------------------------------------------------------------------------- //
// Reports + Chat
// --------------------------------------------------------------------------- //

export type Verdict = "BUY" | "ACCUMULATE" | "HOLD" | "REDUCE" | "SELL";

export interface ReportRecord {
  id: string;
  ticker: string;
  market: string;
  verdict: string | null;
  confidence: string | null;
  composite_score: number | null;
  created_at: string;
}

export interface ReportDetail extends ReportRecord {
  report_data: {
    verdict: string;
    confidence: string;
    composite_score: number;
    executive_summary: string;
    price_summary: string | null;
    news_summary: string | null;
    sentiment_summary: string | null;
    key_catalysts: string[];
    risks: string[];
    opportunities: string[];
    sources: string[];
    [key: string]: unknown;
  };
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

// --------------------------------------------------------------------------- //
// Alerts (Phase 6)
// --------------------------------------------------------------------------- //

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

// --------------------------------------------------------------------------- //
// Filings RAG (Phase 4)
// --------------------------------------------------------------------------- //

export interface FilingCitation {
  citation: string;
  filing_type: string | null;
  fiscal_year: number | null;
  section: string | null;
  page: number | null;
  url: string | null;
  similarity: number;
  excerpt: string;
}

export interface FilingsAnswer {
  ticker: string;
  question: string;
  answer: string;
  citations: FilingCitation[];
  grounded: boolean;
  model_used: string | null;
  fetched_at: string;
}

export interface FilingsStatus {
  ticker: string;
  filing_count: number;
  chunk_count: number;
  filings: {
    id: string;
    source: string;
    filing_type: string | null;
    fiscal_year: number | null;
    title: string | null;
    url: string | null;
    chunk_count: number;
    indexed_at: string | null;
  }[];
}
