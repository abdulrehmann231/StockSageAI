"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth-context";

export default function LandingPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user) router.replace("/dashboard");
  }, [loading, user, router]);

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center gap-8 p-8 text-center">
      <span className="rounded-full border border-border px-3 py-1 text-xs font-medium text-muted-foreground">
        v0.1.0 · Phase 1
      </span>
      <h1 className="text-5xl font-bold tracking-tight">StockSage AI</h1>
      <p className="max-w-xl text-balance text-lg text-muted-foreground">
        Multi-agent AI stock research analyst for Pakistani (PSX) and Global
        (US) markets. Get a Buy / Hold / Sell verdict grounded in real filings,
        news, and sentiment.
      </p>
      <div className="flex gap-3">
        <Link
          href="/signup"
          className="rounded-lg bg-foreground px-5 py-2.5 text-sm font-medium text-background hover:opacity-90"
        >
          Get started
        </Link>
        <Link
          href="/login"
          className="rounded-lg border border-border px-5 py-2.5 text-sm font-medium hover:bg-muted"
        >
          Log in
        </Link>
      </div>
    </main>
  );
}
