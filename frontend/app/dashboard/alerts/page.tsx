"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Card, EmptyState, Field, Spinner, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import type { Alert, AlertType } from "@/lib/types";

const ALERT_TYPES: { value: AlertType; label: string; needs: "pct-neg" | "pct-pos" | "target" | "none" }[] = [
  { value: "PRICE_DROP", label: "Price drops by %", needs: "pct-neg" },
  { value: "PRICE_RISE", label: "Price rises by %", needs: "pct-pos" },
  { value: "PRICE_TARGET", label: "Price hits target", needs: "target" },
  { value: "BIG_NEWS", label: "Big news breaks", needs: "none" },
  { value: "SENTIMENT_SHIFT", label: "Sentiment turns bearish", needs: "none" },
];

export default function AlertsPage() {
  const qc = useQueryClient();
  const [ticker, setTicker] = useState("");
  const [type, setType] = useState<AlertType>("PRICE_DROP");
  const [amount, setAmount] = useState("");
  const [direction, setDirection] = useState<"above" | "below">("above");
  const [error, setError] = useState<string | null>(null);

  const meta = ALERT_TYPES.find((t) => t.value === type)!;

  const list = useQuery({
    queryKey: ["alerts"],
    queryFn: async () => (await api.get<Alert[]>("/api/alerts")).data,
  });

  const create = useMutation({
    mutationFn: async () => {
      let condition: Record<string, unknown> = {};
      if (meta.needs === "pct-neg") condition = { threshold_pct: -Math.abs(Number(amount)) };
      else if (meta.needs === "pct-pos") condition = { threshold_pct: Math.abs(Number(amount)) };
      else if (meta.needs === "target") condition = { target: Number(amount), direction };
      else if (type === "SENTIMENT_SHIFT") condition = { to: "bearish" };
      else if (type === "BIG_NEWS") condition = { impacts: ["HIGH_POSITIVE", "HIGH_NEGATIVE"] };
      await api.post("/api/alerts", {
        ticker: ticker.trim().toUpperCase(),
        alert_type: type,
        condition,
      });
    },
    onSuccess: () => {
      setTicker("");
      setAmount("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: () => setError("Could not create alert — check the ticker and values."),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/api/alerts/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Get notified when a price, news, or sentiment condition fires.
        </p>
      </div>

      <Card>
        <h2 className="mb-3 text-sm font-medium">New alert</h2>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Ticker">
            <input
              className={inputClass}
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="AAPL"
              required
            />
          </Field>
          <Field label="Condition">
            <select
              className={inputClass}
              value={type}
              onChange={(e) => setType(e.target.value as AlertType)}
            >
              {ALERT_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>
          {meta.needs !== "none" && (
            <Field label={meta.needs === "target" ? "Target price" : "Percent"}>
              <input
                className={inputClass}
                type="number"
                step="any"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                required
              />
            </Field>
          )}
          {meta.needs === "target" && (
            <Field label="Direction">
              <select
                className={inputClass}
                value={direction}
                onChange={(e) => setDirection(e.target.value as "above" | "below")}
              >
                <option value="above">above</option>
                <option value="below">below</option>
              </select>
            </Field>
          )}
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </Button>
        </form>
        {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}
      </Card>

      {list.isLoading ? (
        <Spinner />
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState title="No alerts yet" hint="Create one above." />
      ) : (
        <div className="flex flex-col gap-3">
          {list.data.map((alert) => (
            <Card key={alert.id} className="flex items-center justify-between">
              <div>
                <div className="font-medium">
                  {alert.ticker}{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    {alert.alert_type.replace(/_/g, " ").toLowerCase()}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground">
                  {JSON.stringify(alert.condition)}
                  {alert.last_triggered ? " · last fired " + new Date(alert.last_triggered).toLocaleDateString() : ""}
                </div>
              </div>
              <Button
                variant="danger"
                onClick={() => remove.mutate(alert.id)}
                disabled={remove.isPending}
              >
                Delete
              </Button>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
