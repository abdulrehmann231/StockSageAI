"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Button, Card, EmptyState, Field, Spinner, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import type { WatchlistItem } from "@/lib/types";

export default function WatchlistPage() {
  const qc = useQueryClient();
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["watchlist"],
    queryFn: async () => (await api.get<WatchlistItem[]>("/api/watchlist")).data,
  });

  const add = useMutation({
    mutationFn: async () => {
      await api.post("/api/watchlist", { ticker: ticker.trim().toUpperCase() });
    },
    onSuccess: () => {
      setTicker("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["watchlist"] });
    },
    onError: () => setError("Could not add — check the ticker exists."),
  });

  const remove = useMutation({
    mutationFn: async (t: string) => {
      await api.delete(`/api/watchlist/${t}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Watchlist</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Stocks you&apos;re tracking. Add a ticker to keep an eye on it.
        </p>
      </div>

      <Card>
        <form
          className="flex items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <Field label="Add ticker">
            <input
              className={inputClass}
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="AAPL"
              required
            />
          </Field>
          <Button type="submit" disabled={add.isPending}>
            {add.isPending ? "Adding…" : "Add"}
          </Button>
        </form>
        {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}
      </Card>

      {list.isLoading ? (
        <Spinner />
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState title="Your watchlist is empty" hint="Add a ticker above." />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {list.data.map((item) => (
            <Card key={item.ticker} className="flex items-center justify-between">
              <Link
                href={`/dashboard/stocks/${item.ticker}`}
                className="min-w-0"
              >
                <div className="font-medium">{item.ticker}</div>
                <div className="truncate text-xs text-muted-foreground">{item.name}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {item.sector || item.market}
                </div>
              </Link>
              <Button
                variant="danger"
                onClick={() => remove.mutate(item.ticker)}
                disabled={remove.isPending}
              >
                Remove
              </Button>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
