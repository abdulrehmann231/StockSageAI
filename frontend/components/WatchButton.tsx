"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { WatchlistItem } from "@/lib/types";

interface Props {
  ticker: string;
}

export function WatchButton({ ticker }: Props) {
  const queryClient = useQueryClient();

  const { data: watchlist } = useQuery({
    queryKey: ["watchlist"],
    queryFn: async () => {
      const { data } = await api.get<WatchlistItem[]>("/api/watchlist");
      return data;
    },
  });

  const isWatched = !!watchlist?.some((w) => w.ticker === ticker);

  const mutation = useMutation({
    mutationFn: async (watch: boolean) => {
      if (watch) {
        await api.post("/api/watchlist", { ticker });
      } else {
        await api.delete(`/api/watchlist/${encodeURIComponent(ticker)}`);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist"] });
    },
  });

  return (
    <button
      onClick={() => mutation.mutate(!isWatched)}
      disabled={mutation.isPending}
      aria-pressed={isWatched}
      className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 ${
        isWatched
          ? "border-verdict-hold/40 bg-verdict-hold/10 text-verdict-hold"
          : "border-border hover:bg-muted"
      }`}
    >
      <span aria-hidden="true">{isWatched ? "★" : "☆"}</span>
      {mutation.isPending ? "…" : isWatched ? "Watching" : "Watch"}
    </button>
  );
}
