"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { SearchBar } from "@/components/SearchBar";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { WatchlistItem } from "@/lib/types";

function MarketBadge({ market }: { market: string }) {
  const flag = market === "PSX" ? "🇵🇰" : "🇺🇸";
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
      <span aria-hidden="true">{flag}</span>
      <span>{market}</span>
    </span>
  );
}

export default function WatchlistPage() {
  const queryClient = useQueryClient();
  const [ticker, setTicker] = useState("");

  const watchlistQuery = useQuery({
    queryKey: ["watchlist"],
    queryFn: async () => {
      const { data } = await api.get<WatchlistItem[]>("/api/watchlist");
      return data;
    },
  });

  const addMutation = useMutation({
    mutationFn: async (t: string) => {
      const { data } = await api.post("/api/watchlist", {
        ticker: t.trim().toUpperCase(),
      });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist"] });
      setTicker("");
    },
  });

  const removeMutation = useMutation({
    mutationFn: async (t: string) => {
      await api.delete(`/api/watchlist/${encodeURIComponent(t)}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist"] });
    },
  });

  const items = watchlistQuery.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Watchlist</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Track stocks you&apos;re interested in. Add any ticker to your watchlist.
        </p>
      </div>

      <div className="flex gap-2 max-w-md">
        <input
          type="text"
          placeholder="Add ticker (e.g. ENGRO, AAPL)"
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && ticker.trim()) {
              addMutation.mutate(ticker);
            }
          }}
          className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
        />
        <button
          onClick={() => ticker.trim() && addMutation.mutate(ticker)}
          disabled={!ticker.trim() || addMutation.isPending}
          className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
        >
          {addMutation.isPending ? "Adding..." : "Add"}
        </button>
      </div>

      {watchlistQuery.isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-lg bg-muted/30" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-border bg-muted/20 p-8 text-center">
          <p className="text-sm text-muted-foreground">
            Your watchlist is empty. Add a stock above to get started.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <div
              key={item.ticker}
              className="flex items-center justify-between rounded-lg border border-border bg-background p-4"
            >
              <div className="flex items-center gap-4">
                <Link
                  href={`/dashboard/stocks/${encodeURIComponent(item.ticker)}`}
                  className="font-semibold hover:underline"
                >
                  {item.ticker}
                </Link>
                <MarketBadge market={item.market} />
                <span className="text-sm text-muted-foreground">{item.name}</span>
                {item.sector && (
                  <span className="hidden text-xs text-muted-foreground sm:inline">
                    · {item.sector}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-4">
                <span className="text-xs text-muted-foreground">
                  Added {formatDate(item.added_at)}
                </span>
                <button
                  onClick={() => removeMutation.mutate(item.ticker)}
                  disabled={removeMutation.isPending}
                  className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
