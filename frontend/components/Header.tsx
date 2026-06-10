"use client";

import Link from "next/link";

import { useAuth } from "@/lib/auth-context";

export function Header() {
  const { user, logout } = useAuth();
  return (
    <header className="border-b border-border bg-background">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Link href="/dashboard" className="text-lg font-semibold tracking-tight">
          StockSage <span className="text-muted-foreground">AI</span>
        </Link>
        <nav className="flex items-center gap-4 text-sm">
          <Link href="/dashboard" className="text-muted-foreground hover:text-foreground">
            Dashboard
          </Link>
          <Link href="/dashboard/reports" className="text-muted-foreground hover:text-foreground">
            Reports
          </Link>
          <div className="flex items-center gap-3">
            <span className="text-muted-foreground">
              {user?.full_name || user?.email}
            </span>
            <button
              onClick={logout}
              className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted"
            >
              Log out
            </button>
          </div>
        </nav>
      </div>
    </header>
  );
}
