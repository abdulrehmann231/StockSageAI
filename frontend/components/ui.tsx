"use client";

import { cn } from "@/lib/utils";

export function Card({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("rounded-lg border border-border bg-background p-5", className)}>
      {children}
    </div>
  );
}

export function StatCard({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "neutral" | "positive" | "negative";
}) {
  return (
    <Card className="bg-muted/30">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p
        className={cn(
          "mt-2 text-2xl font-semibold tabular-nums",
          tone === "positive" && "text-emerald-600",
          tone === "negative" && "text-red-600"
        )}
      >
        {value}
      </p>
      {sub ? <p className="mt-1 text-xs text-muted-foreground">{sub}</p> : null}
    </Card>
  );
}

const VERDICT_STYLES: Record<string, string> = {
  BUY: "bg-emerald-100 text-emerald-800 border-emerald-200",
  ACCUMULATE: "bg-green-100 text-green-800 border-green-200",
  HOLD: "bg-amber-100 text-amber-800 border-amber-200",
  REDUCE: "bg-orange-100 text-orange-800 border-orange-200",
  SELL: "bg-red-100 text-red-800 border-red-200",
};

export function VerdictBadge({ verdict }: { verdict: string | null }) {
  const key = (verdict || "HOLD").toUpperCase();
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        VERDICT_STYLES[key] ?? "bg-muted text-muted-foreground border-border"
      )}
    >
      {key}
    </span>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
      {label}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/20 p-8 text-center">
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="mt-1 text-sm text-muted-foreground">{hint}</p> : null}
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}

export function Button({
  className,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "outline" | "danger";
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-foreground text-background hover:bg-foreground/90",
        variant === "outline" && "border border-border hover:bg-muted",
        variant === "danger" && "border border-red-200 text-red-700 hover:bg-red-50",
        className
      )}
      {...props}
    />
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

export const inputClass =
  "rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-foreground/20";
