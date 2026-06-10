"use client";

import Link from "next/link";

import { SearchBar } from "@/components/SearchBar";
import { useAuth } from "@/lib/auth-context";

const QUICK_LINKS = [
  {
    href: "/dashboard/reports",
    title: "Reports",
    description: "View and generate AI research reports.",
  },
  {
    href: "/dashboard/watchlist",
    title: "Watchlist",
    description: "Track stocks you're interested in.",
  },
  {
    href: "/dashboard/alerts",
    title: "Alerts",
    description: "Set up price, news, and sentiment alerts.",
  },
];

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

      <section className="grid gap-4 sm:grid-cols-3">
        {QUICK_LINKS.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="rounded-lg border border-border bg-muted/30 p-5 transition-colors hover:bg-muted/50"
          >
            <h2 className="text-sm font-medium">{link.title}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{link.description}</p>
          </Link>
        ))}
      </section>
    </div>
  );
}
