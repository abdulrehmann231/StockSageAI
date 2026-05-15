export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center gap-6 p-8 text-center">
      <span className="rounded-full border border-border px-3 py-1 text-xs font-medium text-muted-foreground">
        v0.1.0 · Phase 0 scaffold
      </span>
      <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
        StockSage AI
      </h1>
      <p className="max-w-xl text-balance text-muted-foreground">
        Multi-agent AI stock research analyst for Pakistani (PSX) and Global
        (US) markets. Frontend is online.
      </p>
    </main>
  );
}
