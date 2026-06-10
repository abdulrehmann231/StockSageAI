"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { api } from "@/lib/api";
import type { WatchlistItem } from "@/lib/types";

export function WatchlistWidget() {
  const query = useQuery({
    queryKey: ["watchlist"],
    queryFn: async () => {
      const { data } = await api.get<WatchlistItem[]>("/api/watchlist");
      return data;
    },
  });

  return (
    <div className="rounded-lg border border-border bg-muted/30 p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">Watchlist</h2>
        <Link
          href="/dashboard/watchlist"
          className="text-xs text-muted-foreground underline hover:text-foreground"
        >
          View all
        </Link>
      </div>

      {query.isLoading && (
        <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
      )}

      {query.data && query.data.length === 0 && (
        <p className="mt-2 text-sm">
          Empty — open a stock and tap <span className="font-medium">Watch</span>.
        </p>
      )}

      {query.data && query.data.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2">
          {query.data.slice(0, 5).map((w) => (
            <li key={w.ticker}>
              <Link
                href={`/dashboard/stocks/${encodeURIComponent(w.ticker)}`}
                className="flex items-center justify-between gap-3 rounded-md border border-border bg-background px-3 py-2 hover:bg-muted/40"
              >
                <span className="text-sm font-semibold">{w.ticker}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {w.name}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
