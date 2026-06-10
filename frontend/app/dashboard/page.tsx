"use client";

import { RecentReports } from "@/components/RecentReports";
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

      <section className="grid gap-4 sm:grid-cols-2">
        <RecentReports />
        <div className="rounded-lg border border-border bg-muted/30 p-5">
          <h2 className="text-sm font-medium text-muted-foreground">
            Watchlist
          </h2>
          <p className="mt-2 text-sm">Empty — coming in Phase 6.</p>
        </div>
      </section>
    </div>
  );
}
