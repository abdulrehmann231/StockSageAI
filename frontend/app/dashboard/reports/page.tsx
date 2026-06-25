"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState } from "react";

import { Button, Card, EmptyState, Field, Spinner, VerdictBadge, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import type { ReportDetail, ReportRecord } from "@/lib/types";

export default function ReportsPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["reports"],
    queryFn: async () => (await api.get<ReportRecord[]>("/api/reports/user")).data,
  });

  const generate = useMutation({
    mutationFn: async () =>
      (await api.post<ReportDetail>("/api/reports/generate", {
        ticker: ticker.trim().toUpperCase(),
      })).data,
    onSuccess: (report) => {
      setTicker("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["reports"] });
      router.push(`/dashboard/reports/${report.id}`);
    },
    onError: () => setError("Could not generate — check the ticker exists."),
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          AI research reports with a Buy/Hold/Sell verdict. Generating one runs the
          multi-agent fan-out, so it can take a moment.
        </p>
      </div>

      <Card>
        <form
          className="flex items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            generate.mutate();
          }}
        >
          <Field label="Generate report for">
            <input
              className={inputClass}
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="AAPL"
              required
            />
          </Field>
          <Button type="submit" disabled={generate.isPending}>
            {generate.isPending ? "Generating…" : "Generate"}
          </Button>
        </form>
        {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}
      </Card>

      {list.isLoading ? (
        <Spinner />
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState title="No reports yet" hint="Generate one above." />
      ) : (
        <div className="flex flex-col gap-3">
          {list.data.map((r) => (
            <Link key={r.id} href={`/dashboard/reports/${r.id}`}>
              <Card className="flex items-center justify-between hover:bg-muted/40">
                <div>
                  <div className="font-medium">{r.ticker}</div>
                  <div className="text-xs text-muted-foreground">
                    {r.market} · {new Date(r.created_at).toLocaleString()}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {r.composite_score !== null && (
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {r.composite_score >= 0 ? "+" : ""}
                      {r.composite_score.toFixed(2)}
                    </span>
                  )}
                  <VerdictBadge verdict={r.verdict} />
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
