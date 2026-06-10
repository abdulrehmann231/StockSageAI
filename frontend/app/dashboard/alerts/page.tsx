"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Alert, AlertType } from "@/lib/types";

const ALERT_TYPES: { value: AlertType; label: string; description: string }[] = [
  { value: "PRICE_DROP", label: "Price Drop", description: "Alert when price drops by a % threshold" },
  { value: "PRICE_RISE", label: "Price Rise", description: "Alert when price rises by a % threshold" },
  { value: "PRICE_TARGET", label: "Price Target", description: "Alert when price crosses a target" },
  { value: "BIG_NEWS", label: "Big News", description: "Alert on high-impact news events" },
  { value: "SENTIMENT_SHIFT", label: "Sentiment Shift", description: "Alert when crowd sentiment changes" },
];

function AlertTypeBadge({ type }: { type: AlertType }) {
  const colors: Record<AlertType, string> = {
    PRICE_DROP: "bg-verdict-sell/10 text-verdict-sell",
    PRICE_RISE: "bg-verdict-buy/10 text-verdict-buy",
    PRICE_TARGET: "bg-blue-500/10 text-blue-500",
    BIG_NEWS: "bg-amber-500/10 text-amber-500",
    SENTIMENT_SHIFT: "bg-purple-500/10 text-purple-500",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${colors[type]}`}>
      {type.replace(/_/g, " ")}
    </span>
  );
}

function CreateAlertForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [ticker, setTicker] = useState("");
  const [alertType, setAlertType] = useState<AlertType>("PRICE_DROP");
  const [thresholdPct, setThresholdPct] = useState("-5");
  const [targetPrice, setTargetPrice] = useState("");
  const [targetDirection, setTargetDirection] = useState<"above" | "below">("above");
  const [cooldown, setCooldown] = useState("24");

  const createMutation = useMutation({
    mutationFn: async () => {
      let condition: Record<string, unknown> = {};

      if (alertType === "PRICE_DROP") {
        condition = { threshold_pct: parseFloat(thresholdPct) || -5 };
      } else if (alertType === "PRICE_RISE") {
        condition = { threshold_pct: Math.abs(parseFloat(thresholdPct)) || 5 };
      } else if (alertType === "PRICE_TARGET") {
        condition = { target: parseFloat(targetPrice) || 0, direction: targetDirection };
      } else if (alertType === "BIG_NEWS") {
        condition = { impacts: ["HIGH_POSITIVE", "HIGH_NEGATIVE"] };
      } else if (alertType === "SENTIMENT_SHIFT") {
        condition = { to: "bullish" };
      }

      const { data } = await api.post("/api/alerts", {
        ticker: ticker.trim().toUpperCase(),
        alert_type: alertType,
        condition,
        cooldown_hours: parseInt(cooldown) || 24,
      });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
      onClose();
    },
  });

  return (
    <div className="rounded-lg border border-border bg-muted/30 p-4 space-y-4">
      <h3 className="text-sm font-semibold">Create New Alert</h3>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Ticker</label>
          <input
            type="text"
            placeholder="e.g. ENGRO, AAPL"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Alert Type</label>
          <select
            value={alertType}
            onChange={(e) => setAlertType(e.target.value as AlertType)}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
          >
            {ALERT_TYPES.map((at) => (
              <option key={at.value} value={at.value}>
                {at.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {(alertType === "PRICE_DROP" || alertType === "PRICE_RISE") && (
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">
            Threshold % {alertType === "PRICE_DROP" ? "(negative)" : "(positive)"}
          </label>
          <input
            type="number"
            value={thresholdPct}
            onChange={(e) => setThresholdPct(e.target.value)}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
          />
        </div>
      )}

      {alertType === "PRICE_TARGET" && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Target Price</label>
            <input
              type="number"
              value={targetPrice}
              onChange={(e) => setTargetPrice(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Direction</label>
            <select
              value={targetDirection}
              onChange={(e) => setTargetDirection(e.target.value as "above" | "below")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
            >
              <option value="above">Above</option>
              <option value="below">Below</option>
            </select>
          </div>
        </div>
      )}

      <div>
        <label className="mb-1 block text-xs text-muted-foreground">
          Cooldown (hours)
        </label>
        <input
          type="number"
          value={cooldown}
          onChange={(e) => setCooldown(e.target.value)}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
        />
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => ticker.trim() && createMutation.mutate()}
          disabled={!ticker.trim() || createMutation.isPending}
          className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
        >
          {createMutation.isPending ? "Creating..." : "Create Alert"}
        </button>
        <button
          onClick={onClose}
          className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:bg-muted"
        >
          Cancel
        </button>
        {createMutation.isError && (
          <span className="text-xs text-verdict-sell">Failed to create alert.</span>
        )}
      </div>
    </div>
  );
}

export default function AlertsPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);

  const alertsQuery = useQuery({
    queryKey: ["alerts"],
    queryFn: async () => {
      const { data } = await api.get<Alert[]>("/api/alerts");
      return data;
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async (alert: Alert) => {
      const { data } = await api.patch(`/api/alerts/${alert.id}`, {
        is_active: !alert.is_active,
      });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/api/alerts/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  const alerts = alertsQuery.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Set up price, news, and sentiment alerts for your stocks.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90"
        >
          Create Alert
        </button>
      </div>

      {showCreate && <CreateAlertForm onClose={() => setShowCreate(false)} />}

      {alertsQuery.isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-20 animate-pulse rounded-lg bg-muted/30" />
          ))}
        </div>
      ) : alerts.length === 0 ? (
        <div className="rounded-lg border border-border bg-muted/20 p-8 text-center">
          <p className="text-sm text-muted-foreground">
            No alerts configured. Create your first alert above.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {alerts.map((alert) => (
            <div
              key={alert.id}
              className={`flex items-center justify-between rounded-lg border border-border bg-background p-4 ${
                !alert.is_active ? "opacity-50" : ""
              }`}
            >
              <div className="flex items-center gap-4">
                <span className="font-semibold">{alert.ticker}</span>
                <AlertTypeBadge type={alert.alert_type} />
                <span className="text-xs text-muted-foreground">
                  {alert.condition.target
                    ? `${alert.condition.direction} ${alert.condition.target}`
                    : typeof alert.condition.threshold_pct === "number"
                      ? `${alert.condition.threshold_pct > 0 ? "+" : ""}${alert.condition.threshold_pct}%`
                      : alert.condition.to
                        ? `→ ${alert.condition.to}`
                        : JSON.stringify(alert.condition)}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">
                  {alert.cooldown_hours}h cooldown
                </span>
                {alert.last_triggered && (
                  <span className="text-xs text-muted-foreground">
                    Last fired: {formatDate(alert.last_triggered)}
                  </span>
                )}
                <button
                  onClick={() => toggleMutation.mutate(alert)}
                  className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
                >
                  {alert.is_active ? "Pause" : "Resume"}
                </button>
                <button
                  onClick={() => deleteMutation.mutate(alert.id)}
                  disabled={deleteMutation.isPending}
                  className="rounded-md border border-verdict-sell/30 px-2 py-1 text-xs text-verdict-sell hover:bg-verdict-sell/10 disabled:opacity-50"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
