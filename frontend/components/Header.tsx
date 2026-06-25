"use client";

import type { Route } from "next";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

const NAV_LINKS: { href: Route; label: string }[] = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/dashboard/portfolio", label: "Portfolio" },
  { href: "/dashboard/watchlist", label: "Watchlist" },
  { href: "/dashboard/reports", label: "Reports" },
  { href: "/dashboard/alerts", label: "Alerts" },
];

export function Header() {
  const { user, logout } = useAuth();
  const pathname = usePathname();

  return (
    <header className="border-b border-border bg-background">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <div className="flex items-center gap-6">
          <Link href="/dashboard" className="text-lg font-semibold tracking-tight">
            StockSage <span className="text-muted-foreground">AI</span>
          </Link>
          <nav className="hidden items-center gap-4 text-sm md:flex">
            {NAV_LINKS.map((link) => {
              const active =
                link.href === "/dashboard"
                  ? pathname === "/dashboard"
                  : pathname.startsWith(link.href);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={cn(
                    "hover:text-foreground",
                    active ? "font-medium text-foreground" : "text-muted-foreground"
                  )}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="hidden text-muted-foreground sm:inline">
            {user?.full_name || user?.email}
          </span>
          <button
            onClick={logout}
            className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
