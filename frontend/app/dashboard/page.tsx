"use client";

import type { Route } from "next";
import Link from "next/link";

import { SearchBar } from "@/components/SearchBar";
import { useAuth } from "@/lib/auth-context";

export default function DashboardPage() {
  const { user } = useAuth();

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">
          Welcome{user?.full_name ? `, ${user.full_name.split(" ")[0]}` : ""}.
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Search a ticker or company name to start your research.
        </p>
      </section>

      <section className="max-w-2xl">
        <SearchBar />
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { href: "/dashboard/portfolio", title: "Portfolio", hint: "Track holdings + live P&L" },
          { href: "/dashboard/watchlist", title: "Watchlist", hint: "Stocks you're following" },
          { href: "/dashboard/reports", title: "Reports", hint: "AI Buy/Hold/Sell verdicts" },
          { href: "/dashboard/alerts", title: "Alerts", hint: "Price, news & sentiment triggers" },
        ].map((card) => (
          <Link
            key={card.href}
            href={card.href as Route}
            className="rounded-lg border border-border bg-muted/30 p-5 hover:bg-muted/50"
          >
            <h2 className="text-sm font-medium">{card.title}</h2>
            <p className="mt-2 text-sm text-muted-foreground">{card.hint}</p>
          </Link>
        ))}
      </section>
    </div>
  );
}
