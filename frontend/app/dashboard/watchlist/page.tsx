"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { WatchlistItem } from "@/lib/types";

function MarketBadge({ market }: { market: string }) {
  const flag = market === "PSX" ? "🇵🇰" : "🇺🇸";
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      <span aria-hidden="true">{flag}</span>
      <span>{market}</span>
    </span>
  );
}

export default function WatchlistPage() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["watchlist"],
    queryFn: async () => {
      const { data } = await api.get<WatchlistItem[]>("/api/watchlist");
      return data;
    },
  });

  const removeMutation = useMutation({
    mutationFn: async (ticker: string) => {
      await api.delete(`/api/watchlist/${encodeURIComponent(ticker)}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist"] });
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Watchlist</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Stocks you&apos;re tracking.
        </p>
      </div>

      {query.isLoading && (
        <div className="flex flex-col gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-lg bg-muted/30" />
          ))}
        </div>
      )}

      {query.isError && (
        <div className="rounded-lg border border-verdict-sell/30 bg-verdict-sell/10 p-4 text-sm text-verdict-sell">
          Couldn&apos;t load your watchlist.
        </div>
      )}

      {query.data && query.data.length === 0 && (
        <div className="rounded-lg border border-border bg-muted/20 p-8 text-center text-sm text-muted-foreground">
          Nothing here yet. Open a stock and tap{" "}
          <span className="font-medium">Watch</span> to track it.
        </div>
      )}

      {query.data && query.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {query.data.map((w) => (
            <li
              key={w.ticker}
              className="flex items-center justify-between gap-4 rounded-lg border border-border bg-background p-4"
            >
              <Link
                href={`/dashboard/stocks/${encodeURIComponent(w.ticker)}`}
                className="flex min-w-0 flex-1 items-center gap-3 hover:opacity-80"
              >
                <span className="text-base font-semibold">{w.ticker}</span>
                <MarketBadge market={w.market} />
                <span className="truncate text-sm text-muted-foreground">
                  {w.name}
                </span>
              </Link>
              <div className="flex shrink-0 items-center gap-4">
                <span className="hidden text-xs text-muted-foreground sm:inline">
                  Added {formatDateTime(w.added_at)}
                </span>
                <button
                  onClick={() => removeMutation.mutate(w.ticker)}
                  disabled={removeMutation.isPending}
                  className="rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
                >
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
