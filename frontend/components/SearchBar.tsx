"use client";

import { useQuery } from "@tanstack/react-query";
import Fuse, { type IFuseOptions } from "fuse.js";
import Link from "next/link";
import { useId, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { Stock } from "@/lib/types";

const FUSE_OPTIONS: IFuseOptions<Stock> = {
  keys: [
    { name: "ticker", weight: 0.6 },
    { name: "name", weight: 0.4 },
  ],
  threshold: 0.35,
  ignoreLocation: true,
};

function MarketBadge({ market }: { market: string }) {
  const flag = market === "PSX" ? "🇵🇰" : "🇺🇸";
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
      <span aria-hidden="true">{flag}</span>
      <span>{market}</span>
    </span>
  );
}

export function SearchBar() {
  const [query, setQuery] = useState("");
  const listboxId = useId();

  const { data: stocks = [], isLoading } = useQuery({
    queryKey: ["stocks", "list"],
    queryFn: async () => {
      const { data } = await api.get<Stock[]>("/api/stocks");
      return data;
    },
    staleTime: 1000 * 60 * 60,
  });

  const fuse = useMemo(() => new Fuse(stocks, FUSE_OPTIONS), [stocks]);

  const results = useMemo(() => {
    if (!query.trim()) return [];
    return fuse.search(query).slice(0, 8).map((r) => r.item);
  }, [fuse, query]);

  const dropdownOpen = query.trim().length > 0 && !isLoading;

  return (
    <div className="w-full">
      <label htmlFor={`${listboxId}-input`} className="sr-only">
        Search stocks
      </label>
      <div className="relative">
        <input
          id={`${listboxId}-input`}
          type="text"
          role="combobox"
          aria-controls={listboxId}
          aria-expanded={dropdownOpen}
          aria-autocomplete="list"
          placeholder={
            isLoading
              ? "Loading stocks..."
              : "Search ticker or company (e.g. ENGRO, AAPL, Lucky Cement)"
          }
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={isLoading}
          className="w-full rounded-lg border border-border bg-background px-4 py-3 text-sm shadow-sm outline-none focus:ring-2 focus:ring-foreground disabled:opacity-50"
        />
        {dropdownOpen && results.length > 0 ? (
          <ul
            id={listboxId}
            role="listbox"
            aria-label="Stock search results"
            className="absolute z-10 mt-2 w-full overflow-hidden rounded-lg border border-border bg-background shadow-lg"
          >
            {results.map((s) => (
              <li key={s.ticker} role="option" aria-selected="false">
                <Link
                  href={`/dashboard/stocks/${encodeURIComponent(s.ticker)}`}
                  onClick={() => setQuery("")}
                  className="flex items-center justify-between px-4 py-2.5 hover:bg-muted"
                >
                  <div className="flex flex-col">
                    <span className="font-medium">{s.ticker}</span>
                    <span className="text-xs text-muted-foreground">
                      {s.name}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {s.sector ? (
                      <span className="hidden text-xs text-muted-foreground sm:inline">
                        {s.sector}
                      </span>
                    ) : null}
                    <MarketBadge market={s.market} />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
        {dropdownOpen && results.length === 0 ? (
          <p
            id={listboxId}
            role="status"
            aria-live="polite"
            className="absolute mt-2 w-full rounded-lg border border-border bg-background px-4 py-3 text-sm text-muted-foreground shadow-lg"
          >
            No matches for &quot;{query}&quot;.
          </p>
        ) : null}
      </div>
    </div>
  );
}
