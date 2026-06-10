"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { useState } from "react";

import { api } from "@/lib/api";
import {
  ALERT_TYPE_LABELS,
  NEWS_IMPACT_OPTIONS,
  buildCondition,
} from "@/lib/alerts";
import type { Alert, AlertType } from "@/lib/types";

const ALERT_TYPES = Object.keys(ALERT_TYPE_LABELS) as AlertType[];

export function AlertForm() {
  const queryClient = useQueryClient();

  const [ticker, setTicker] = useState("");
  const [alertType, setAlertType] = useState<AlertType>("PRICE_DROP");
  const [thresholdPct, setThresholdPct] = useState(5);
  const [target, setTarget] = useState(0);
  const [direction, setDirection] = useState<"above" | "below">("above");
  const [impacts, setImpacts] = useState<string[]>([
    "HIGH_POSITIVE",
    "HIGH_NEGATIVE",
  ]);
  const [to, setTo] = useState<"bullish" | "bearish" | "neutral">("bullish");
  const [cooldownHours, setCooldownHours] = useState(24);

  const mutation = useMutation({
    mutationFn: async () => {
      const condition = buildCondition(alertType, {
        thresholdPct,
        target,
        direction,
        impacts,
        to,
      });
      const { data } = await api.post<Alert>("/api/alerts", {
        ticker: ticker.trim().toUpperCase(),
        alert_type: alertType,
        condition,
        cooldown_hours: cooldownHours,
      });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
      setTicker("");
    },
  });

  const errorMessage =
    mutation.error instanceof AxiosError
      ? (mutation.error.response?.data as { detail?: string })?.detail ??
        mutation.error.message
      : null;

  function toggleImpact(impact: string) {
    setImpacts((prev) =>
      prev.includes(impact)
        ? prev.filter((i) => i !== impact)
        : [...prev, impact]
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (ticker.trim()) mutation.mutate();
      }}
      className="flex flex-col gap-4 rounded-xl border border-border bg-background p-5 shadow-sm"
    >
      <h2 className="text-sm font-semibold">New alert</h2>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Ticker
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="e.g. AAPL or HBL"
            className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
            required
          />
        </label>

        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Alert type
          <select
            value={alertType}
            onChange={(e) => setAlertType(e.target.value as AlertType)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
          >
            {ALERT_TYPES.map((t) => (
              <option key={t} value={t}>
                {ALERT_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Type-specific fields */}
      {(alertType === "PRICE_DROP" || alertType === "PRICE_RISE") && (
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground sm:max-w-xs">
          Threshold (%)
          <input
            type="number"
            min={0}
            step={0.5}
            value={thresholdPct}
            onChange={(e) => setThresholdPct(Number(e.target.value))}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
          />
          <span className="font-normal text-muted-foreground">
            Notify when the price {alertType === "PRICE_DROP" ? "falls" : "rises"} by
            this much.
          </span>
        </label>
      )}

      {alertType === "PRICE_TARGET" && (
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            Target price
            <input
              type="number"
              min={0}
              step="any"
              value={target}
              onChange={(e) => setTarget(Number(e.target.value))}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            Direction
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value as "above" | "below")}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
            >
              <option value="above">Goes above</option>
              <option value="below">Goes below</option>
            </select>
          </label>
        </div>
      )}

      {alertType === "BIG_NEWS" && (
        <div className="flex flex-col gap-2 text-xs font-medium text-muted-foreground">
          News impact levels
          <div className="flex flex-wrap gap-2">
            {NEWS_IMPACT_OPTIONS.map((impact) => (
              <button
                key={impact}
                type="button"
                onClick={() => toggleImpact(impact)}
                className={`rounded-full border px-3 py-1 text-xs font-medium ${
                  impacts.includes(impact)
                    ? "border-foreground bg-foreground text-background"
                    : "border-border text-muted-foreground hover:bg-muted"
                }`}
              >
                {impact.replace("_", " ").toLowerCase()}
              </button>
            ))}
          </div>
        </div>
      )}

      {alertType === "SENTIMENT_SHIFT" && (
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground sm:max-w-xs">
          Notify when sentiment shifts to
          <select
            value={to}
            onChange={(e) =>
              setTo(e.target.value as "bullish" | "bearish" | "neutral")
            }
            className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
          >
            <option value="bullish">Bullish</option>
            <option value="bearish">Bearish</option>
            <option value="neutral">Neutral</option>
          </select>
        </label>
      )}

      <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground sm:max-w-xs">
        Cooldown (hours)
        <input
          type="number"
          min={0}
          max={336}
          value={cooldownHours}
          onChange={(e) => setCooldownHours(Number(e.target.value))}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
        />
        <span className="font-normal text-muted-foreground">
          Minimum gap between repeat notifications.
        </span>
      </label>

      {errorMessage && (
        <p className="text-sm text-verdict-sell">{errorMessage}</p>
      )}

      <div>
        <button
          type="submit"
          disabled={mutation.isPending || !ticker.trim()}
          className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
        >
          {mutation.isPending ? "Creating…" : "Create alert"}
        </button>
      </div>
    </form>
  );
}
