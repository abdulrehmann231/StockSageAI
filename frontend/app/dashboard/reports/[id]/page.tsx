"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { notFound, useParams } from "next/navigation";
import { AxiosError } from "axios";

import { ChatPanel } from "@/components/ChatPanel";
import { ReportView } from "@/components/ReportView";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ReportDetail } from "@/lib/types";

export default function ReportDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id ?? "";

  const query = useQuery({
    queryKey: ["report", id],
    queryFn: async () => {
      const { data } = await api.get<ReportDetail>(
        `/api/reports/${encodeURIComponent(id)}`
      );
      return data;
    },
    enabled: !!id,
    retry: (failureCount, error) => {
      if (error instanceof AxiosError && error.response?.status === 404) {
        return false;
      }
      return failureCount < 2;
    },
  });

  if (
    query.isError &&
    query.error instanceof AxiosError &&
    query.error.response?.status === 404
  ) {
    notFound();
  }

  if (query.isLoading || !query.data) {
    return (
      <div className="flex flex-col gap-6">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-64 animate-pulse rounded-xl bg-muted/30" />
      </div>
    );
  }

  const record = query.data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">{record.ticker}</h1>
            <span className="text-sm text-muted-foreground">{record.market}</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Generated {formatDateTime(record.created_at)}
          </p>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <Link
            href={`/dashboard/stocks/${encodeURIComponent(record.ticker)}`}
            className="text-muted-foreground underline hover:text-foreground"
          >
            View stock
          </Link>
          <Link
            href="/dashboard/reports"
            className="text-muted-foreground underline hover:text-foreground"
          >
            ← All reports
          </Link>
        </div>
      </div>

      <ReportView report={record.report_data} />

      <ChatPanel reportId={record.id} />
    </div>
  );
}
