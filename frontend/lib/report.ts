import type { ConfidenceLevel, NewsImpact, Verdict } from "@/lib/types";

/** Tailwind class bundle for a verdict pill (border + bg + text). */
export function verdictClasses(verdict: Verdict | null | undefined): string {
  switch (verdict) {
    case "BUY":
    case "ACCUMULATE":
      return "border-verdict-buy/30 bg-verdict-buy/10 text-verdict-buy";
    case "SELL":
    case "REDUCE":
      return "border-verdict-sell/30 bg-verdict-sell/10 text-verdict-sell";
    case "HOLD":
    default:
      return "border-verdict-hold/30 bg-verdict-hold/10 text-verdict-hold";
  }
}

/** Hex color for a verdict — useful for inline styles (e.g. score gauge). */
export function verdictColor(verdict: Verdict | null | undefined): string {
  switch (verdict) {
    case "BUY":
    case "ACCUMULATE":
      return "#16a34a";
    case "SELL":
    case "REDUCE":
      return "#dc2626";
    case "HOLD":
    default:
      return "#ca8a04";
  }
}

const IMPACT_LABELS: Record<NewsImpact, string> = {
  HIGH_POSITIVE: "Very Positive",
  MEDIUM_POSITIVE: "Positive",
  NEUTRAL: "Neutral",
  MEDIUM_NEGATIVE: "Negative",
  HIGH_NEGATIVE: "Very Negative",
};

export function impactLabel(impact: NewsImpact): string {
  return IMPACT_LABELS[impact] ?? impact;
}

export function impactClasses(impact: NewsImpact): string {
  switch (impact) {
    case "HIGH_POSITIVE":
    case "MEDIUM_POSITIVE":
      return "border-verdict-buy/30 bg-verdict-buy/10 text-verdict-buy";
    case "HIGH_NEGATIVE":
    case "MEDIUM_NEGATIVE":
      return "border-verdict-sell/30 bg-verdict-sell/10 text-verdict-sell";
    case "NEUTRAL":
    default:
      return "border-border bg-muted text-muted-foreground";
  }
}

export function confidenceLabel(confidence: ConfidenceLevel | null | undefined): string {
  if (!confidence) return "—";
  return `${confidence[0].toUpperCase()}${confidence.slice(1)} confidence`;
}

/** Sentiment score in [-1, 1] → tailwind text color. */
export function sentimentColor(score: number): string {
  if (score > 0.15) return "text-verdict-buy";
  if (score < -0.15) return "text-verdict-sell";
  return "text-verdict-hold";
}
