import type { AlertType } from "@/lib/types";

export const ALERT_TYPE_LABELS: Record<AlertType, string> = {
  PRICE_DROP: "Price drop",
  PRICE_RISE: "Price rise",
  PRICE_TARGET: "Price target",
  BIG_NEWS: "Big news",
  SENTIMENT_SHIFT: "Sentiment shift",
};

export const NEWS_IMPACT_OPTIONS = [
  "HIGH_POSITIVE",
  "MEDIUM_POSITIVE",
  "MEDIUM_NEGATIVE",
  "HIGH_NEGATIVE",
] as const;

/** Render an alert's condition into a short, human-readable sentence. */
export function describeCondition(
  alertType: AlertType,
  condition: Record<string, unknown>
): string {
  switch (alertType) {
    case "PRICE_DROP":
      return `Drops ${Math.abs(Number(condition.threshold_pct ?? 0))}% or more`;
    case "PRICE_RISE":
      return `Rises ${Number(condition.threshold_pct ?? 0)}% or more`;
    case "PRICE_TARGET":
      return `Price goes ${String(condition.direction ?? "above")} ${condition.target ?? "?"}`;
    case "BIG_NEWS": {
      const impacts = Array.isArray(condition.impacts)
        ? (condition.impacts as string[])
        : ["HIGH_POSITIVE", "HIGH_NEGATIVE"];
      return `High-impact news (${impacts.map((i) => i.replace("_", " ").toLowerCase()).join(", ")})`;
    }
    case "SENTIMENT_SHIFT": {
      const to = String(condition.to ?? "bullish");
      const from = condition.from ? `from ${condition.from} ` : "";
      return `Sentiment shifts ${from}to ${to}`;
    }
    default:
      return JSON.stringify(condition);
  }
}

/** Build a valid `condition` payload for a given alert type from form fields. */
export function buildCondition(
  alertType: AlertType,
  fields: {
    thresholdPct?: number;
    target?: number;
    direction?: "above" | "below";
    impacts?: string[];
    to?: "bullish" | "bearish" | "neutral";
  }
): Record<string, unknown> {
  switch (alertType) {
    case "PRICE_DROP":
      return { threshold_pct: -Math.abs(fields.thresholdPct ?? 5) };
    case "PRICE_RISE":
      return { threshold_pct: Math.abs(fields.thresholdPct ?? 5) };
    case "PRICE_TARGET":
      return { target: fields.target ?? 0, direction: fields.direction ?? "above" };
    case "BIG_NEWS":
      return {
        impacts: fields.impacts?.length
          ? fields.impacts
          : ["HIGH_POSITIVE", "HIGH_NEGATIVE"],
      };
    case "SENTIMENT_SHIFT":
      return { to: fields.to ?? "bullish" };
    default:
      return {};
  }
}
