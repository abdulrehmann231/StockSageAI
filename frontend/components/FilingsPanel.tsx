"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Card, Field, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import type { FilingsAnswer, FilingsStatus } from "@/lib/types";

export function FilingsPanel({ ticker }: { ticker: string }) {
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<FilingsAnswer | null>(null);

  const status = useQuery({
    queryKey: ["filings", ticker, "status"],
    queryFn: async () =>
      (await api.get<FilingsStatus>(`/api/filings/${ticker}/status`)).data,
  });

  const index = useMutation({
    mutationFn: async () => {
      await api.post(`/api/filings/${ticker}/index`, { limit: 1 });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["filings", ticker, "status"] }),
  });

  const ask = useMutation({
    mutationFn: async () =>
      (await api.post<FilingsAnswer>(`/api/filings/${ticker}/ask`, {
        question: question.trim(),
        k: 5,
      })).data,
    onSuccess: (data) => setAnswer(data),
  });

  const indexed = (status.data?.chunk_count ?? 0) > 0;

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium">Ask the filings</h2>
        <Button variant="outline" onClick={() => index.mutate()} disabled={index.isPending}>
          {index.isPending ? "Indexing…" : indexed ? "Re-index" : "Index filings"}
        </Button>
      </div>

      <p className="mb-3 text-xs text-muted-foreground">
        {indexed
          ? `${status.data?.chunk_count} chunks across ${status.data?.filing_count} filing(s) indexed.`
          : "No filings indexed yet — index first, then ask grounded questions."}
      </p>

      <form
        className="flex items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          ask.mutate();
        }}
      >
        <Field label="Question">
          <input
            className={`${inputClass} w-72`}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What are the main risk factors?"
            required
          />
        </Field>
        <Button type="submit" disabled={ask.isPending}>
          {ask.isPending ? "Thinking…" : "Ask"}
        </Button>
      </form>

      {answer && (
        <div className="mt-4 space-y-3 text-sm">
          <p className={answer.grounded ? "" : "text-muted-foreground"}>{answer.answer}</p>
          {answer.citations.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">Sources</p>
              {answer.citations.map((c, i) => (
                <div key={i} className="rounded-md border border-border bg-muted/30 p-2 text-xs">
                  <div className="font-medium">
                    {c.citation}{" "}
                    <span className="text-muted-foreground">
                      · similarity {(c.similarity * 100).toFixed(0)}%
                    </span>
                  </div>
                  <p className="mt-1 text-muted-foreground">{c.excerpt}…</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
