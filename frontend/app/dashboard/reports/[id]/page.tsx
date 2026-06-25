"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { FilingsPanel } from "@/components/FilingsPanel";
import { Button, Card, Spinner, VerdictBadge, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import type { ChatMessage, ReportDetail } from "@/lib/types";

export default function ReportDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const report = useQuery({
    queryKey: ["report", id],
    queryFn: async () => (await api.get<ReportDetail>(`/api/reports/${id}`)).data,
  });

  if (report.isLoading) return <Spinner label="Loading report…" />;
  if (report.isError || !report.data) {
    return (
      <div className="text-sm">
        <p>Report not found.</p>
        <Link href="/dashboard/reports" className="text-muted-foreground hover:text-foreground">
          ← Back to reports
        </Link>
      </div>
    );
  }

  const r = report.data;
  const d = r.report_data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <Link
            href="/dashboard/reports"
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← Reports
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {r.ticker} <span className="text-muted-foreground">{r.market}</span>
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            confidence: {d.confidence}
          </span>
          <VerdictBadge verdict={d.verdict} />
        </div>
      </div>

      <Card>
        <h2 className="mb-2 text-sm font-medium">Executive summary</h2>
        <p className="text-sm">{d.executive_summary}</p>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        <Section title="Price" body={d.price_summary} />
        <Section title="News" body={d.news_summary} />
        <Section title="Sentiment" body={d.sentiment_summary} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <ListCard title="Opportunities" items={d.opportunities} tone="positive" />
        <ListCard title="Risks" items={d.risks} tone="negative" />
      </div>

      {d.key_catalysts.length > 0 && (
        <Card>
          <h2 className="mb-2 text-sm font-medium">Key catalysts</h2>
          <div className="flex flex-wrap gap-2">
            {d.key_catalysts.map((c, i) => (
              <span key={i} className="rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs">
                {c}
              </span>
            ))}
          </div>
        </Card>
      )}

      <FilingsPanel ticker={r.ticker} />
      <ChatPanel reportId={id} ticker={r.ticker} />

      {d.sources.length > 0 && (
        <p className="text-xs text-muted-foreground">Sources: {d.sources.join(", ")}</p>
      )}
    </div>
  );
}

function Section({ title, body }: { title: string; body: string | null }) {
  return (
    <Card>
      <h2 className="mb-2 text-sm font-medium">{title}</h2>
      <p className="whitespace-pre-line text-sm text-muted-foreground">
        {body || "—"}
      </p>
    </Card>
  );
}

function ListCard({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "positive" | "negative";
}) {
  return (
    <Card>
      <h2 className={`mb-2 text-sm font-medium ${tone === "positive" ? "text-emerald-700" : "text-red-700"}`}>
        {title}
      </h2>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">None identified.</p>
      ) : (
        <ul className="ml-4 list-disc text-sm text-muted-foreground">
          {items.map((it, i) => (
            <li key={i}>{it}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ChatPanel({ reportId, ticker }: { reportId: string; ticker: string }) {
  const qc = useQueryClient();
  const [message, setMessage] = useState("");

  const history = useQuery({
    queryKey: ["chat", reportId],
    queryFn: async () =>
      (await api.get<ChatMessage[]>(`/api/chat/${reportId}/history`)).data,
  });

  const send = useMutation({
    mutationFn: async () => {
      await api.post(`/api/chat/${reportId}/message`, { content: message.trim() });
    },
    onSuccess: () => {
      setMessage("");
      qc.invalidateQueries({ queryKey: ["chat", reportId] });
    },
  });

  return (
    <Card>
      <h2 className="mb-3 text-sm font-medium">Chat with {ticker}</h2>
      <div className="mb-3 max-h-80 space-y-3 overflow-y-auto">
        {history.data && history.data.length > 0 ? (
          history.data.map((m) => (
            <div
              key={m.id}
              className={m.role === "user" ? "text-right" : "text-left"}
            >
              <span
                className={`inline-block max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                  m.role === "user"
                    ? "bg-foreground text-background"
                    : "bg-muted text-foreground"
                }`}
              >
                {m.content}
              </span>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            Ask a follow-up — e.g. “What&apos;s the P/E?” or “Why this verdict?”
          </p>
        )}
      </div>
      <form
        className="flex items-center gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          send.mutate();
        }}
      >
        <input
          className={`${inputClass} flex-1`}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Ask about this report…"
          required
        />
        <Button type="submit" disabled={send.isPending}>
          {send.isPending ? "…" : "Send"}
        </Button>
      </form>
    </Card>
  );
}
