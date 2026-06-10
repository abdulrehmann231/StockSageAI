"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { VerdictBadge } from "@/components/VerdictBadge";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ReportRecord } from "@/lib/types";

export function RecentReports() {
  const query = useQuery({
    queryKey: ["reports", "user"],
    queryFn: async () => {
      const { data } = await api.get<ReportRecord[]>("/api/reports/user");
      return data;
    },
  });

  return (
    <div className="rounded-lg border border-border bg-muted/30 p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">
          Recent reports
        </h2>
        <Link
          href="/dashboard/reports"
          className="text-xs text-muted-foreground underline hover:text-foreground"
        >
          View all
        </Link>
      </div>

      {query.isLoading && (
        <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
      )}

      {query.data && query.data.length === 0 && (
        <p className="mt-2 text-sm">No reports yet. Search a stock above.</p>
      )}

      {query.data && query.data.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2">
          {query.data.slice(0, 5).map((r) => (
            <li key={r.id}>
              <Link
                href={`/dashboard/reports/${r.id}`}
                className="flex items-center justify-between gap-3 rounded-md border border-border bg-background px-3 py-2 hover:bg-muted/40"
              >
                <span className="flex items-center gap-2">
                  <span className="text-sm font-semibold">{r.ticker}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {formatDateTime(r.created_at)}
                  </span>
                </span>
                <VerdictBadge verdict={r.verdict} size="sm" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
