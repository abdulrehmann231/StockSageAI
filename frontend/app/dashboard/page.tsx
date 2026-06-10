"use client";

import { RecentReports } from "@/components/RecentReports";
import { SearchBar } from "@/components/SearchBar";
import { WatchlistWidget } from "@/components/WatchlistWidget";
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
        <WatchlistWidget />
      </section>
    </div>
  );
}
