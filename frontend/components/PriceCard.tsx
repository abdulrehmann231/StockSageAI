"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import {
  formatNumber,
  formatPercent,
  formatPrice,
  formatTime,
} from "@/lib/format";
import type { PriceQuote, Stock } from "@/lib/types";

interface Props {
  stock: Stock;
}

function MetricRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}

export function PriceCard({ stock }: Props) {
  const query = useQuery({
    queryKey: ["price", stock.ticker],
    queryFn: async () => {
      const { data } = await api.get<PriceQuote>(
        `/api/stocks/${encodeURIComponent(stock.ticker)}/price`
      );
      return data;
    },
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    staleTime: 0,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-xl border border-border bg-muted/20 p-6 text-sm text-muted-foreground">
        Loading price for {stock.ticker}...
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="rounded-xl border border-verdict-sell/30 bg-verdict-sell/10 p-6 text-sm text-verdict-sell">
        Couldn&apos;t load price. {query.error instanceof Error ? query.error.message : "Unknown error."}
      </div>
    );
  }

  const q = query.data;
  const up = (q.change ?? 0) >= 0;
  const deltaColor = up ? "text-verdict-buy" : "text-verdict-sell";
  const deltaSign = up ? "▲" : "▼";

  return (
    <div className="rounded-xl border border-border bg-background p-6 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="text-3xl font-bold tabular-nums">
            {formatPrice(q.price, q.currency)}
          </div>
          {q.change !== null && q.change_pct !== null ? (
            <div className={`mt-1 text-sm font-medium ${deltaColor}`}>
              {deltaSign} {formatNumber(Math.abs(q.change), { digits: 2 })} ({formatPercent(q.change_pct)})
            </div>
          ) : null}
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>via {q.source}{q.cached ? " · cached" : ""}</div>
          <div>updated {formatTime(q.fetched_at)}</div>
        </div>
      </div>

      <div className="mt-4 grid gap-x-8 gap-y-1 sm:grid-cols-2">
        <MetricRow
          label="Previous close"
          value={q.previous_close !== null ? formatPrice(q.previous_close, q.currency) : "—"}
        />
        <MetricRow
          label="Open"
          value={q.open !== null ? formatPrice(q.open, q.currency) : "—"}
        />
        <MetricRow
          label="Day high"
          value={q.day_high !== null ? formatPrice(q.day_high, q.currency) : "—"}
        />
        <MetricRow
          label="Day low"
          value={q.day_low !== null ? formatPrice(q.day_low, q.currency) : "—"}
        />
        <MetricRow
          label="52w high"
          value={q.week_52_high !== null ? formatPrice(q.week_52_high, q.currency) : "—"}
        />
        <MetricRow
          label="52w low"
          value={q.week_52_low !== null ? formatPrice(q.week_52_low, q.currency) : "—"}
        />
        <MetricRow label="Volume" value={formatNumber(q.volume, { compact: true })} />
        <MetricRow label="Market cap" value={formatNumber(q.market_cap, { compact: true })} />
        <MetricRow label="P/E" value={formatNumber(q.pe_ratio)} />
        <MetricRow label="EPS" value={formatNumber(q.eps)} />
        <MetricRow
          label="Dividend yield"
          value={q.dividend_yield !== null ? formatPercent(q.dividend_yield) : "—"}
        />
      </div>
    </div>
  );
}
