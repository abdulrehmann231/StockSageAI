"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { PortfolioChart, SectorAllocationChart } from "@/components/portfolio/Charts";
import { Button, Card, EmptyState, Field, Spinner, StatCard, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/format";
import type {
  Performance,
  Portfolio,
  PortfolioAnalysis,
  TaxEstimate,
} from "@/lib/types";

export default function PortfolioPage() {
  const qc = useQueryClient();

  const portfolio = useQuery({
    queryKey: ["portfolio"],
    queryFn: async () => (await api.get<Portfolio>("/api/portfolio")).data,
  });

  const tax = useQuery({
    queryKey: ["portfolio", "tax"],
    queryFn: async () => (await api.get<TaxEstimate>("/api/portfolio/tax-estimate")).data,
  });

  const performance = useQuery({
    queryKey: ["portfolio", "performance"],
    queryFn: async () =>
      (await api.get<Performance>("/api/portfolio/performance?range=90d")).data,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["portfolio"] });
  };

  if (portfolio.isLoading) return <Spinner label="Loading portfolio…" />;

  const p = portfolio.data;
  const metrics = p?.metrics;
  const holdings = p?.holdings ?? [];
  const gainTone =
    (metrics?.total_gain_loss ?? 0) > 0
      ? "positive"
      : (metrics?.total_gain_loss ?? 0) < 0
        ? "negative"
        : "neutral";

  return (
    <div className="flex flex-col gap-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Live P&amp;L across {metrics?.holdings_count ?? 0} position
            {metrics?.holdings_count === 1 ? "" : "s"}.
          </p>
        </div>
        <Button variant="outline" onClick={() => invalidate()}>
          Refresh
        </Button>
      </div>

      {/* Summary */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total value"
          value={formatNumber(metrics?.total_value ?? 0)}
          sub={`${metrics?.priced_count ?? 0}/${metrics?.holdings_count ?? 0} priced`}
        />
        <StatCard
          label="Cost basis"
          value={formatNumber(metrics?.total_cost_basis ?? 0)}
        />
        <StatCard
          label="Total gain / loss"
          value={formatNumber(metrics?.total_gain_loss ?? 0)}
          sub={formatPercent(metrics?.total_gain_loss_pct ?? 0)}
          tone={gainTone}
        />
        <StatCard
          label="Best / worst"
          value={
            metrics?.best_performer
              ? `${metrics.best_performer.ticker} ${formatPercent(metrics.best_performer.gain_loss_pct ?? 0)}`
              : "—"
          }
          sub={
            metrics?.worst_performer
              ? `${metrics.worst_performer.ticker} ${formatPercent(metrics.worst_performer.gain_loss_pct ?? 0)}`
              : undefined
          }
        />
      </div>

      {p && p.errors.length > 0 && (
        <Card className="border-amber-200 bg-amber-50 text-sm text-amber-800">
          Some prices could not be fetched: {p.errors.join("; ")}
        </Card>
      )}

      <AddHoldingForm onAdded={invalidate} />

      {/* Holdings table */}
      {holdings.length === 0 ? (
        <EmptyState
          title="No holdings yet"
          hint="Add a position above to start tracking live P&L."
        />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead className="border-b border-border text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-4 py-3">Ticker</th>
                <th className="px-4 py-3 text-right">Qty</th>
                <th className="px-4 py-3 text-right">Avg cost</th>
                <th className="px-4 py-3 text-right">Price</th>
                <th className="px-4 py-3 text-right">Value</th>
                <th className="px-4 py-3 text-right">Gain / loss</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {holdings.map((h) => {
                const tone =
                  (h.gain_loss ?? 0) > 0
                    ? "text-emerald-600"
                    : (h.gain_loss ?? 0) < 0
                      ? "text-red-600"
                      : "";
                return (
                  <tr key={h.id} className="border-b border-border/60 last:border-0">
                    <td className="px-4 py-3">
                      <div className="font-medium">{h.ticker}</div>
                      <div className="text-xs text-muted-foreground">
                        {h.sector || h.market}
                        {h.is_delisted ? " · delisted" : ""}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatNumber(h.quantity)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatNumber(h.avg_buy_price)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {h.current_price === null ? "—" : formatNumber(h.current_price)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {h.current_value === null ? "—" : formatNumber(h.current_value)}
                    </td>
                    <td className={`px-4 py-3 text-right tabular-nums ${tone}`}>
                      {h.gain_loss === null ? (
                        <span className="text-muted-foreground">no price</span>
                      ) : (
                        <>
                          {formatNumber(h.gain_loss)}
                          <span className="block text-xs">{formatPercent(h.gain_loss_pct ?? 0)}</span>
                        </>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <DeleteHoldingButton id={h.id} onDeleted={invalidate} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}

      {/* Charts */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-4 text-sm font-medium">Sector allocation</h2>
          <SectorAllocationChart allocation={metrics?.sector_allocation ?? {}} />
        </Card>
        <Card>
          <h2 className="mb-4 text-sm font-medium">Performance (90d)</h2>
          <PortfolioChart points={performance.data?.points ?? []} />
        </Card>
      </div>

      {/* Tax + analysis */}
      <div className="grid gap-6 lg:grid-cols-2">
        <TaxCard tax={tax.data} loading={tax.isLoading} />
        <AnalysisPanel />
      </div>
    </div>
  );
}

function AddHoldingForm({ onAdded }: { onAdded: () => void }) {
  const [ticker, setTicker] = useState("");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [buyDate, setBuyDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      await api.post("/api/portfolio/holdings", {
        ticker: ticker.trim().toUpperCase(),
        quantity: Number(quantity),
        avg_buy_price: Number(price),
        buy_date: buyDate || null,
      });
    },
    onSuccess: () => {
      setTicker("");
      setQuantity("");
      setPrice("");
      setBuyDate("");
      setError(null);
      onAdded();
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Could not add holding.";
      setError(typeof detail === "string" ? detail : "Could not add holding.");
    },
  });

  return (
    <Card>
      <h2 className="mb-3 text-sm font-medium">Add holding</h2>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
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
        <Field label="Quantity">
          <input
            className={inputClass}
            type="number"
            step="any"
            min="0"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            required
          />
        </Field>
        <Field label="Avg buy price">
          <input
            className={inputClass}
            type="number"
            step="any"
            min="0"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            required
          />
        </Field>
        <Field label="Buy date (optional)">
          <input
            className={inputClass}
            type="date"
            value={buyDate}
            onChange={(e) => setBuyDate(e.target.value)}
          />
        </Field>
        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? "Adding…" : "Add"}
        </Button>
      </form>
      {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}
    </Card>
  );
}

function DeleteHoldingButton({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const mutation = useMutation({
    mutationFn: async () => {
      await api.delete(`/api/portfolio/holdings/${id}`);
    },
    onSuccess: onDeleted,
  });
  return (
    <Button variant="danger" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
      Remove
    </Button>
  );
}

function TaxCard({ tax, loading }: { tax: TaxEstimate | undefined; loading: boolean }) {
  return (
    <Card>
      <h2 className="mb-3 text-sm font-medium">Tax estimate (if sold today)</h2>
      {loading ? (
        <Spinner />
      ) : !tax || tax.lots.length === 0 ? (
        <p className="text-sm text-muted-foreground">No active holdings to estimate.</p>
      ) : (
        <>
          <div className="mb-3 flex items-baseline gap-2">
            <span className="text-2xl font-semibold tabular-nums">
              {formatNumber(tax.total_estimated_tax)}
            </span>
            <span className="text-xs text-muted-foreground">estimated CGT</span>
          </div>
          <ul className="space-y-1 text-sm">
            {tax.lots.map((lot) => (
              <li key={lot.holding_id} className="flex justify-between gap-3">
                <span className="text-muted-foreground">
                  {lot.ticker}
                  {lot.is_long_term ? " (LT)" : lot.is_long_term === false ? " (ST)" : ""}
                </span>
                <span className="tabular-nums">
                  {lot.estimated_tax === null ? "—" : formatNumber(lot.estimated_tax)}
                </span>
              </li>
            ))}
          </ul>
          {tax.currency_note ? (
            <p className="mt-3 text-xs text-amber-700">{tax.currency_note}</p>
          ) : null}
        </>
      )}
    </Card>
  );
}

function AnalysisPanel() {
  const qc = useQueryClient();
  const latest = useQuery({
    queryKey: ["portfolio", "analysis"],
    queryFn: async () => {
      try {
        return (await api.get<PortfolioAnalysis>("/api/portfolio/analyses/latest")).data;
      } catch {
        return null;
      }
    },
  });

  const analyze = useMutation({
    mutationFn: async () =>
      (await api.post<PortfolioAnalysis>("/api/portfolio/analyze")).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "analysis"] }),
  });

  const data = analyze.data ?? latest.data;
  const a = data?.analysis_data;

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium">AI portfolio analysis</h2>
        <Button onClick={() => analyze.mutate()} disabled={analyze.isPending}>
          {analyze.isPending ? "Analyzing…" : "Analyze my portfolio"}
        </Button>
      </div>
      {analyze.isError ? (
        <p className="text-sm text-red-600">
          Add at least one holding before running analysis.
        </p>
      ) : !data ? (
        <p className="text-sm text-muted-foreground">
          Run the analyst agent for a health score and rebalancing advice.
        </p>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-3">
            <div className="text-3xl font-semibold tabular-nums">{data.health_score}</div>
            <div className="text-xs text-muted-foreground">/ 100 health score</div>
          </div>
          {a?.summary ? <p>{a.summary}</p> : null}
          {a?.recommendations && a.recommendations.length > 0 && (
            <div>
              <p className="font-medium">Recommendations</p>
              <ul className="ml-4 list-disc text-muted-foreground">
                {a.recommendations.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          {a?.concentration_warnings && a.concentration_warnings.length > 0 && (
            <div>
              <p className="font-medium text-amber-700">Concentration</p>
              <ul className="ml-4 list-disc text-amber-700">
                {a.concentration_warnings.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          {a?.tax_loss_opportunities && a.tax_loss_opportunities.length > 0 && (
            <div>
              <p className="font-medium">Tax-loss harvesting</p>
              <ul className="ml-4 list-disc text-muted-foreground">
                {a.tax_loss_opportunities.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
