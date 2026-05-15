"use client";

import Link from "next/link";
import { useState } from "react";
import { AxiosError } from "axios";

import { useAuth } from "@/lib/auth-context";
import type { Market, RiskProfile } from "@/lib/types";

const MARKETS: Market[] = ["PSX", "GLOBAL", "BOTH"];
const RISK_PROFILES: RiskProfile[] = ["Conservative", "Moderate", "Aggressive"];

export default function SignupPage() {
  const { signup } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [defaultMarket, setDefaultMarket] = useState<Market>("BOTH");
  const [riskProfile, setRiskProfile] = useState<RiskProfile>("Moderate");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signup({
        email,
        password,
        full_name: fullName || undefined,
        default_market: defaultMarket,
        risk_profile: riskProfile,
      });
    } catch (err) {
      const msg =
        err instanceof AxiosError
          ? (err.response?.data as { detail?: string } | undefined)?.detail ??
            err.message
          : "Signup failed";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 p-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Create account</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick your default market and risk profile to personalize reports.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Full name</span>
          <input
            type="text"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Email</span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Password</span>
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
          />
          <span className="text-xs text-muted-foreground">
            Min 8 characters.
          </span>
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Default market</span>
            <select
              value={defaultMarket}
              onChange={(e) => setDefaultMarket(e.target.value as Market)}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
            >
              {MARKETS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Risk profile</span>
            <select
              value={riskProfile}
              onChange={(e) => setRiskProfile(e.target.value as RiskProfile)}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-foreground"
            >
              {RISK_PROFILES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        </div>

        {error ? (
          <p className="rounded-md border border-verdict-sell/30 bg-verdict-sell/10 px-3 py-2 text-sm text-verdict-sell">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? "Creating..." : "Create account"}
        </button>
      </form>

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-foreground underline">
          Log in
        </Link>
      </p>
    </main>
  );
}
