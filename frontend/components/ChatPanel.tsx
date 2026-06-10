"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { ChatMessage, ChatTurn } from "@/lib/types";

interface Props {
  reportId: string;
}

const SUGGESTIONS = [
  "What's the P/E?",
  "Summarize the verdict",
  "What are the main risks?",
  "How's the sentiment?",
];

function Bubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm leading-relaxed ${
          isUser
            ? "bg-foreground text-background"
            : "border border-border bg-muted/40 text-foreground"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

export function ChatPanel({ reportId }: Props) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const historyQuery = useQuery({
    queryKey: ["chat", reportId],
    queryFn: async () => {
      const { data } = await api.get<ChatMessage[]>(
        `/api/chat/${reportId}/history`
      );
      return data;
    },
  });

  const mutation = useMutation({
    mutationFn: async (content: string) => {
      const { data } = await api.post<ChatTurn>(
        `/api/chat/${reportId}/message`,
        { content }
      );
      return data;
    },
    onSuccess: (turn) => {
      queryClient.setQueryData<ChatMessage[]>(["chat", reportId], (prev) => [
        ...(prev ?? []),
        turn.user_message,
        turn.assistant_message,
      ]);
    },
  });

  const messages = historyQuery.data ?? [];

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length, mutation.isPending]);

  function send(text: string) {
    const content = text.trim();
    if (!content || mutation.isPending) return;
    setInput("");
    mutation.mutate(content);
  }

  const errorMessage =
    mutation.error instanceof AxiosError
      ? (mutation.error.response?.data as { detail?: string })?.detail ??
        mutation.error.message
      : null;

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border bg-background p-5 shadow-sm">
      <div>
        <h3 className="text-sm font-semibold">Ask about this report</h3>
        <p className="text-xs text-muted-foreground">
          Follow-up questions are answered from this report&apos;s data.
        </p>
      </div>

      <div
        ref={scrollRef}
        className="flex max-h-96 min-h-[6rem] flex-col gap-3 overflow-y-auto"
      >
        {historyQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading conversation…</p>
        )}

        {!historyQuery.isLoading && messages.length === 0 && (
          <div className="flex flex-wrap gap-2 py-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => send(s)}
                className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground hover:bg-muted"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.map((m) => (
          <Bubble key={m.id} message={m} />
        ))}

        {mutation.isPending && (
          <div className="flex justify-start">
            <div className="rounded-2xl border border-border bg-muted/40 px-4 py-2 text-sm text-muted-foreground">
              Thinking…
            </div>
          </div>
        )}
      </div>

      {errorMessage && (
        <p className="text-xs text-verdict-sell">{errorMessage}</p>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="flex items-center gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question…"
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
        />
        <button
          type="submit"
          disabled={mutation.isPending || !input.trim()}
          className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </section>
  );
}
