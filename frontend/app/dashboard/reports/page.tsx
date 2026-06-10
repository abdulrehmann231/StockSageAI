"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { SearchBar } from "@/components/SearchBar";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ReportRecord } from "@/lib/types";

function VerdictBadge({ verdict }: { verdict: string | null }) {
  if (!verdict) return <span className="text-xs text-muted-foreground">—</span>;
  const color =
    verdict === "BUY" || verdict === "ACCUMULATE"
      ? "bg-verdict-buy/10 text-verdict-buy"
      : verdict === "SELL" || verdict === "REDUCE"
        ? "bg-verdict-sell/10 text-verdict-sell"
        : "bg-verdict-hold/10 text-verdict-hold";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>
      {verdict}
    </span>
  );
}

export default function ReportsPage() {
  const queryClient = useQueryClient();
  const [showGenerate, setShowGenerate] = useState(false);
  const [ticker, setTicker] = useState("");

  const reportsQuery = useQuery({
    queryKey: ["reports", "user"],
    queryFn: async () => {
      const { data } = await api.get<ReportRecord[]>("/api/reports/user");
      return data;
    },
  });

  const generateMutation = useMutation({
    mutationFn: async (t: string) => {
      const { data } = await api.post("/api/reports/generate", {
        ticker: t.trim().toUpperCase(),
      });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reports", "user"] });
      setShowGenerate(false);
      setTicker("");
    },
  });

  const reports = reportsQuery.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            AI-generated research reports for your tracked stocks.
          </p>
        </div>
        <button
          onClick={() => setShowGenerate(!showGenerate)}
          className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90"
        >
          Generate Report
        </button>
      </div>

      {showGenerate && (
        <div className="rounded-lg border border-border bg-muted/30 p-4">
          <label className="mb-2 block text-sm font-medium text-muted-foreground">
            Enter ticker to generate a report
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="e.g. ENGRO, AAPL"
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && ticker.trim()) {
                  generateMutation.mutate(ticker);
                }
              }}
              className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
            />
            <button
              onClick={() => ticker.trim() && generateMutation.mutate(ticker)}
              disabled={!ticker.trim() || generateMutation.isPending}
              className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
            >
              {generateMutation.isPending ? "Generating..." : "Generate"}
            </button>
          </div>
          {generateMutation.isError && (
            <p className="mt-2 text-xs text-verdict-sell">
              Generation failed. Please try again.
            </p>
          )}
        </div>
      )}

      {reportsQuery.isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-20 animate-pulse rounded-lg bg-muted/30" />
          ))}
        </div>
      ) : reports.length === 0 ? (
        <div className="rounded-lg border border-border bg-muted/20 p-8 text-center">
          <p className="text-sm text-muted-foreground">
            No reports yet. Generate your first report above.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {reports.map((r) => (
            <Link
              key={r.id}
              href={`/dashboard/reports/${r.id}`}
              className="flex items-center justify-between rounded-lg border border-border bg-background p-4 hover:bg-muted/30 transition-colors"
            >
              <div className="flex items-center gap-4">
                <div>
                  <span className="font-semibold">{r.ticker}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{r.market}</span>
                </div>
                <VerdictBadge verdict={r.verdict} />
                {r.composite_score !== null && (
                  <span className="text-xs text-muted-foreground">
                    Score: {r.composite_score > 0 ? "+" : ""}
                    {r.composite_score.toFixed(2)}
                  </span>
                )}
              </div>
              <div className="text-right">
                <span className="text-xs text-muted-foreground">
                  {formatDate(r.created_at)}
                </span>
                {r.confidence && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    · {r.confidence} confidence
                  </span>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
