"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";

import { api } from "@/lib/api";
import { formatDate, formatRelativeTime } from "@/lib/format";
import type { ChatMessage, ReportDetail, StockReport } from "@/lib/types";

function VerdictBadge({ verdict }: { verdict: string }) {
  const color =
    verdict === "BUY" || verdict === "ACCUMULATE"
      ? "bg-verdict-buy/10 text-verdict-buy border-verdict-buy/30"
      : verdict === "SELL" || verdict === "REDUCE"
        ? "bg-verdict-sell/10 text-verdict-sell border-verdict-sell/30"
        : "bg-verdict-hold/10 text-verdict-hold border-verdict-hold/30";
  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-semibold ${color}`}>
      {verdict}
    </span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-background p-4">
      <h3 className="mb-2 text-sm font-semibold text-muted-foreground">{title}</h3>
      <div className="text-sm leading-relaxed">{children}</div>
    </div>
  );
}

function BulletList({ items }: { items: string[] }) {
  if (items.length === 0) return <span className="text-muted-foreground">None identified.</span>;
  return (
    <ul className="list-disc space-y-1 pl-4">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

function ChatPanel({ reportId }: { reportId: string }) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const historyQuery = useQuery({
    queryKey: ["chat", reportId],
    queryFn: async () => {
      const { data } = await api.get<ChatMessage[]>(`/api/chat/${reportId}/history`);
      return data;
    },
  });

  const sendMutation = useMutation({
    mutationFn: async (content: string) => {
      const { data } = await api.post(`/api/chat/${reportId}/message`, { content });
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData<ChatMessage[]>(["chat", reportId], (old) => {
        if (!old) return [data.user_message, data.assistant_message];
        return [...old, data.user_message, data.assistant_message];
      });
      setInput("");
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    },
  });

  const messages = historyQuery.data ?? [];

  return (
    <div className="rounded-lg border border-border bg-background">
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold">Chat about this report</h3>
      </div>
      <div className="flex h-80 flex-col overflow-y-auto p-4">
        {messages.length === 0 && (
          <p className="flex-1 text-center text-sm text-muted-foreground">
            Ask a question about this report...
          </p>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`mb-3 flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === "user"
                  ? "bg-foreground text-background"
                  : "bg-muted text-foreground"
              }`}
            >
              <p className="whitespace-pre-wrap">{msg.content}</p>
              <p className="mt-1 text-[10px] opacity-60">{formatRelativeTime(msg.created_at)}</p>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="border-t border-border p-3">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Ask about this report..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && input.trim() && !sendMutation.isPending) {
                sendMutation.mutate(input.trim());
              }
            }}
            disabled={sendMutation.isPending}
            className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground disabled:opacity-50"
          />
          <button
            onClick={() => input.trim() && sendMutation.mutate(input.trim())}
            disabled={!input.trim() || sendMutation.isPending}
            className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ReportDetailPage() {
  const params = useParams<{ id: string }>();

  const reportQuery = useQuery({
    queryKey: ["report", params.id],
    queryFn: async () => {
      const { data } = await api.get<ReportDetail>(`/api/reports/${params.id}`);
      return data;
    },
    enabled: !!params.id,
  });

  if (reportQuery.isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-64 animate-pulse rounded-xl bg-muted/30" />
      </div>
    );
  }

  if (reportQuery.isError || !reportQuery.data) {
    return (
      <div className="flex flex-col gap-4">
        <p className="text-verdict-sell">Report not found.</p>
        <Link href="/dashboard/reports" className="text-sm text-muted-foreground underline">
          ← Back to reports
        </Link>
      </div>
    );
  }

  const detail = reportQuery.data;
  const report = detail.report_data as unknown as StockReport;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">{report.ticker}</h1>
            <VerdictBadge verdict={report.verdict} />
          </div>
          <p className="mt-1 text-muted-foreground">
            {report.company_name || detail.ticker} · {detail.market}
          </p>
          <p className="text-xs text-muted-foreground">
            Generated {formatDate(detail.created_at)} · Confidence: {report.confidence} ·
            Composite: {report.composite_score > 0 ? "+" : ""}
            {report.composite_score.toFixed(2)}
          </p>
        </div>
        <Link
          href="/dashboard/reports"
          className="text-sm text-muted-foreground underline hover:text-foreground"
        >
          ← Back
        </Link>
      </div>

      <Section title="Executive Summary">
        <p>{report.executive_summary}</p>
      </Section>

      {report.price_summary && (
        <Section title="Price Analysis">{report.price_summary}</Section>
      )}

      {report.news_summary && (
        <Section title="News Analysis">
          <pre className="whitespace-pre-wrap font-sans">{report.news_summary}</pre>
        </Section>
      )}

      {report.sentiment_summary && (
        <Section title="Sentiment Analysis">
          <pre className="whitespace-pre-wrap font-sans">{report.sentiment_summary}</pre>
        </Section>
      )}

      <div className="grid gap-4 sm:grid-cols-3">
        <Section title="Key Catalysts">
          <BulletList items={report.key_catalysts} />
        </Section>
        <Section title="Risks">
          <BulletList items={report.risks} />
        </Section>
        <Section title="Opportunities">
          <BulletList items={report.opportunities} />
        </Section>
      </div>

      {report.errors.length > 0 && (
        <Section title="Errors">
          <BulletList items={report.errors} />
        </Section>
      )}

      {report.sources.length > 0 && (
        <div className="rounded-lg border border-border bg-muted/20 p-4 text-xs text-muted-foreground">
          Sources: {report.sources.join(", ")}
        </div>
      )}

      <ChatPanel reportId={detail.id} />
    </div>
  );
}
