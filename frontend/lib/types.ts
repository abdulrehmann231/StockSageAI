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
  access_token: string;
  token_type: string;
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
