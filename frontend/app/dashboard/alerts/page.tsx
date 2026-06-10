"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { AlertForm } from "@/components/AlertForm";
import { api } from "@/lib/api";
import { ALERT_TYPE_LABELS, describeCondition } from "@/lib/alerts";
import { formatDateTime } from "@/lib/format";
import type { Alert } from "@/lib/types";

function AlertRow({ alert }: { alert: Alert }) {
  const queryClient = useQueryClient();

  const toggle = useMutation({
    mutationFn: async () => {
      await api.patch(`/api/alerts/${alert.id}`, { is_active: !alert.is_active });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const remove = useMutation({
    mutationFn: async () => {
      await api.delete(`/api/alerts/${alert.id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });

  return (
    <li className="flex items-center justify-between gap-4 rounded-lg border border-border bg-background p-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Link
            href={`/dashboard/stocks/${encodeURIComponent(alert.ticker)}`}
            className="text-base font-semibold hover:underline"
          >
            {alert.ticker}
          </Link>
          <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {ALERT_TYPE_LABELS[alert.alert_type]}
          </span>
          {!alert.is_active && (
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              paused
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {describeCondition(alert.alert_type, alert.condition)}
        </p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          Cooldown {alert.cooldown_hours}h ·{" "}
          {alert.last_triggered
            ? `last fired ${formatDateTime(alert.last_triggered)}`
            : "never fired"}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          onClick={() => toggle.mutate()}
          disabled={toggle.isPending}
          className="rounded-md border border-border px-2.5 py-1 text-xs hover:bg-muted disabled:opacity-50"
        >
          {alert.is_active ? "Pause" : "Resume"}
        </button>
        <button
          onClick={() => remove.mutate()}
          disabled={remove.isPending}
          className="rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
        >
          Delete
        </button>
      </div>
    </li>
  );
}

export default function AlertsPage() {
  const query = useQuery({
    queryKey: ["alerts"],
    queryFn: async () => {
      const { data } = await api.get<Alert[]>("/api/alerts");
      return data;
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Get notified when price, news, or sentiment conditions are met.
        </p>
      </div>

      <AlertForm />

      <div className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold">Your alerts</h2>

        {query.isLoading && (
          <div className="flex flex-col gap-3">
            {[0, 1].map((i) => (
              <div key={i} className="h-20 animate-pulse rounded-lg bg-muted/30" />
            ))}
          </div>
        )}

        {query.isError && (
          <div className="rounded-lg border border-verdict-sell/30 bg-verdict-sell/10 p-4 text-sm text-verdict-sell">
            Couldn&apos;t load your alerts.
          </div>
        )}

        {query.data && query.data.length === 0 && (
          <div className="rounded-lg border border-border bg-muted/20 p-8 text-center text-sm text-muted-foreground">
            No alerts yet. Create one above.
          </div>
        )}

        {query.data && query.data.length > 0 && (
          <ul className="flex flex-col gap-2">
            {query.data.map((a) => (
              <AlertRow key={a.id} alert={a} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
