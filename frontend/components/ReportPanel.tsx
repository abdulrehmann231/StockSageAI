"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AxiosError } from "axios";

import { ReportView } from "@/components/ReportView";
import { api } from "@/lib/api";
import type { ReportDetail } from "@/lib/types";

interface Props {
  ticker: string;
}

const STEPS = [
  "Fetching live price…",
  "Scanning news & filings…",
  "Reading social sentiment…",
  "Synthesizing verdict…",
];

function GeneratingState() {
  const [step, setStep] = useState(0);
  // Advance faux-progress steps on an interval while the request is in flight.
  useEffect(() => {
    const id = setInterval(() => {
      setStep((s) => Math.min(s + 1, STEPS.length - 1));
    }, 2500);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-muted/20 p-6">
      <div className="flex items-center gap-3">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
        <span className="text-sm font-medium">Running multi-agent research…</span>
      </div>
      <ul className="ml-7 flex flex-col gap-1.5 text-xs text-muted-foreground">
        {STEPS.map((s, i) => (
          <li key={i} className={i <= step ? "text-foreground" : ""}>
            {i < step ? "✓ " : i === step ? "→ " : "  "}
            {s}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ReportPanel({ ticker }: Props) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<ReportDetail | null>(null);

  const mutation = useMutation({
    mutationFn: async (refresh: boolean) => {
      const { data } = await api.post<ReportDetail>("/api/reports/generate", {
        ticker,
        refresh,
      });
      return data;
    },
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({ queryKey: ["reports", "user"] });
    },
  });

  const errorMessage =
    mutation.error instanceof AxiosError
      ? (mutation.error.response?.data as { detail?: string })?.detail ??
        mutation.error.message
      : mutation.error instanceof Error
        ? mutation.error.message
        : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">AI research report</h2>
        <div className="flex items-center gap-2">
          {result && (
            <Link
              href={`/dashboard/reports/${result.id}`}
              className="text-xs text-muted-foreground underline hover:text-foreground"
            >
              Open full page ↗
            </Link>
          )}
          <button
            onClick={() => mutation.mutate(!!result)}
            disabled={mutation.isPending}
            className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
          >
            {mutation.isPending
              ? "Generating…"
              : result
                ? "Regenerate"
                : "Generate report"}
          </button>
        </div>
      </div>

      {mutation.isPending && <GeneratingState />}

      {errorMessage && !mutation.isPending && (
        <div className="rounded-lg border border-verdict-sell/30 bg-verdict-sell/10 p-4 text-sm text-verdict-sell">
          {errorMessage}
        </div>
      )}

      {result && !mutation.isPending && (
        <ReportView report={result.report_data} />
      )}

      {!result && !mutation.isPending && !errorMessage && (
        <div className="rounded-lg border border-border bg-muted/20 p-6 text-sm text-muted-foreground">
          Generate an AI report to get a verdict, news, and sentiment synthesis
          across our research agents.
        </div>
      )}
    </div>
  );
}
