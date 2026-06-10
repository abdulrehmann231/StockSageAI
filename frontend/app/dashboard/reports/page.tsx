"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { VerdictBadge } from "@/components/VerdictBadge";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ReportRecord } from "@/lib/types";

function MarketBadge({ market }: { market: string }) {
  const flag = market === "PSX" ? "🇵🇰" : "🇺🇸";
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      <span aria-hidden="true">{flag}</span>
      <span>{market}</span>
    </span>
  );
}

export default function ReportsListPage() {
  const query = useQuery({
    queryKey: ["reports", "user"],
    queryFn: async () => {
      const { data } = await api.get<ReportRecord[]>("/api/reports/user");
      return data;
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your saved multi-agent research reports.
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
          Couldn&apos;t load reports.
        </div>
      )}

      {query.data && query.data.length === 0 && (
        <div className="rounded-lg border border-border bg-muted/20 p-8 text-center text-sm text-muted-foreground">
          No reports yet. Open a stock and click{" "}
          <span className="font-medium">Generate report</span>.
        </div>
      )}

      {query.data && query.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {query.data.map((r) => (
            <li key={r.id}>
              <Link
                href={`/dashboard/reports/${r.id}`}
                className="flex items-center justify-between gap-4 rounded-lg border border-border bg-background p-4 hover:bg-muted/30"
              >
                <div className="flex items-center gap-3">
                  <span className="text-base font-semibold">{r.ticker}</span>
                  <MarketBadge market={r.market} />
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-xs text-muted-foreground">
                    {formatDateTime(r.created_at)}
                  </span>
                  <VerdictBadge verdict={r.verdict} size="sm" />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
