"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { notFound, useParams } from "next/navigation";
import { AxiosError } from "axios";

import { PriceCard } from "@/components/PriceCard";
import { api } from "@/lib/api";
import type { Stock } from "@/lib/types";

function MarketBadge({ market }: { market: string }) {
  const flag = market === "PSX" ? "🇵🇰" : "🇺🇸";
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      <span aria-hidden="true">{flag}</span>
      <span>{market}</span>
    </span>
  );
}

export default function StockDetailPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = (params.ticker ?? "").toUpperCase();

  const query = useQuery({
    queryKey: ["stock", ticker],
    queryFn: async () => {
      const { data } = await api.get<Stock>(
        `/api/stocks/${encodeURIComponent(ticker)}`
      );
      return data;
    },
    enabled: !!ticker,
    retry: (failureCount, error) => {
      if (error instanceof AxiosError && error.response?.status === 404) {
        return false;
      }
      return failureCount < 2;
    },
  });

  if (query.isError && query.error instanceof AxiosError && query.error.response?.status === 404) {
    notFound();
  }

  if (query.isLoading || !query.data) {
    return (
      <div className="flex flex-col gap-6">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-64 animate-pulse rounded-xl bg-muted/30" />
      </div>
    );
  }

  const stock = query.data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">{stock.ticker}</h1>
            <MarketBadge market={stock.market} />
          </div>
          <p className="mt-1 text-muted-foreground">{stock.name}</p>
          <p className="text-xs text-muted-foreground">
            {[stock.sector, stock.industry].filter(Boolean).join(" · ") || "—"}
          </p>
        </div>
        <Link
          href="/dashboard"
          className="text-sm text-muted-foreground underline hover:text-foreground"
        >
          ← Back
        </Link>
      </div>

      <PriceCard stock={stock} />

      <div className="rounded-lg border border-border bg-muted/20 p-4 text-xs text-muted-foreground">
        Auto-refreshes every 30 seconds. Full multi-agent research report
        comes in Phase 5.
      </div>
    </div>
  );
}
