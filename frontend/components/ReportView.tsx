import { VerdictBadge } from "@/components/VerdictBadge";
import { formatTime } from "@/lib/format";
import {
  confidenceLabel,
  impactClasses,
  impactLabel,
  sentimentColor,
  verdictColor,
} from "@/lib/report";
import type { StockReport } from "@/lib/types";

interface Props {
  report: StockReport;
}

function ScoreGauge({ score, verdict }: { score: number; verdict: StockReport["verdict"] }) {
  // composite_score is a signed signal in [-1, 1]; map onto a centered bar.
  const clamped = Math.max(-1, Math.min(1, score));
  const color = verdictColor(verdict);
  // Fill grows from the 50% center toward whichever side the score leans.
  const half = (Math.abs(clamped) / 2) * 100;
  const left = clamped >= 0 ? 50 : 50 - half;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between text-xs text-muted-foreground">
        <span>Signal score</span>
        <span className="font-semibold tabular-nums" style={{ color }}>
          {clamped >= 0 ? "+" : ""}
          {clamped.toFixed(2)}
        </span>
      </div>
      <div className="relative h-2 w-full overflow-hidden rounded-full bg-muted">
        {/* center reference line */}
        <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-border" />
        <div
          className="absolute top-0 h-full transition-all"
          style={{ left: `${left}%`, width: `${half}%`, backgroundColor: color }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>Bearish</span>
        <span>Bullish</span>
      </div>
    </div>
  );
}

function BulletList({
  title,
  items,
  tone = "neutral",
}: {
  title: string;
  items: string[];
  tone?: "positive" | "negative" | "neutral";
}) {
  if (!items.length) return null;
  const dot =
    tone === "positive"
      ? "text-verdict-buy"
      : tone === "negative"
        ? "text-verdict-sell"
        : "text-muted-foreground";
  return (
    <div>
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h4>
      <ul className="flex flex-col gap-1.5">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-sm">
            <span className={`mt-1 ${dot}`} aria-hidden="true">
              •
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-background p-5 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

export function ReportView({ report }: Props) {
  const news = report.news;
  const sentiment = report.sentiment;

  return (
    <div className="flex flex-col gap-5">
      {/* Verdict header */}
      <div className="rounded-xl border border-border bg-background p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <VerdictBadge verdict={report.verdict} size="lg" />
              <span className="text-sm text-muted-foreground">
                {confidenceLabel(report.confidence)}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {report.company_name ?? report.ticker} · {report.market}
              {report.model_used ? ` · ${report.model_used}` : ""} · updated{" "}
              {formatTime(report.fetched_at)}
              {report.cached ? " · cached" : ""}
            </p>
          </div>
          <div className="w-full max-w-xs sm:w-56">
            <ScoreGauge score={report.composite_score} verdict={report.verdict} />
          </div>
        </div>
        <p className="mt-4 border-t border-border pt-4 text-sm leading-relaxed">
          {report.executive_summary}
        </p>
      </div>

      {/* Catalysts / risks / opportunities */}
      {(report.key_catalysts.length ||
        report.risks.length ||
        report.opportunities.length) > 0 && (
        <Section title="Key takeaways">
          <div className="grid gap-5 sm:grid-cols-3">
            <BulletList title="Catalysts" items={report.key_catalysts} tone="neutral" />
            <BulletList title="Opportunities" items={report.opportunities} tone="positive" />
            <BulletList title="Risks" items={report.risks} tone="negative" />
          </div>
        </Section>
      )}

      {/* Price */}
      {report.price_summary && (
        <Section title="Price analysis">
          <p className="text-sm leading-relaxed text-muted-foreground">
            {report.price_summary}
          </p>
        </Section>
      )}

      {/* News */}
      {(report.news_summary || news) && (
        <Section title="News">
          {report.news_summary && (
            <p className="mb-4 text-sm leading-relaxed text-muted-foreground">
              {report.news_summary}
            </p>
          )}
          {news && news.articles.length > 0 && (
            <ul className="flex flex-col divide-y divide-border">
              {news.articles.map((a, i) => (
                <li key={i} className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0">
                  <div className="flex items-start justify-between gap-3">
                    <a
                      href={a.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-medium hover:underline"
                    >
                      {a.title}
                    </a>
                    <span
                      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${impactClasses(a.impact)}`}
                    >
                      {impactLabel(a.impact)}
                    </span>
                  </div>
                  {a.summary && (
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {a.summary}
                    </p>
                  )}
                  <span className="text-[10px] text-muted-foreground">
                    {a.source}
                    {a.published_at ? ` · ${formatTime(a.published_at)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {/* Sentiment */}
      {(report.sentiment_summary || sentiment) && (
        <Section title="Social sentiment">
          {report.sentiment_summary && (
            <p className="mb-4 text-sm leading-relaxed text-muted-foreground">
              {report.sentiment_summary}
            </p>
          )}
          {sentiment && (
            <>
              <div className="mb-4 flex flex-wrap items-center gap-6 text-sm">
                <div>
                  <span className="text-muted-foreground">Overall: </span>
                  <span className={`font-semibold ${sentimentColor(sentiment.overall_sentiment)}`}>
                    {sentiment.label} ({sentiment.overall_sentiment.toFixed(2)})
                  </span>
                </div>
                <div className="text-muted-foreground">
                  <span className="text-verdict-buy font-medium">
                    {sentiment.bullish_pct.toFixed(0)}% bullish
                  </span>{" "}
                  ·{" "}
                  <span className="text-verdict-sell font-medium">
                    {sentiment.bearish_pct.toFixed(0)}% bearish
                  </span>{" "}
                  · {sentiment.post_count} posts
                </div>
              </div>
              <div className="grid gap-5 sm:grid-cols-2">
                <BulletList
                  title="Bullish points"
                  items={sentiment.top_bullish_points}
                  tone="positive"
                />
                <BulletList
                  title="Bearish points"
                  items={sentiment.top_bearish_points}
                  tone="negative"
                />
              </div>
            </>
          )}
        </Section>
      )}

      {/* Sources / errors */}
      {(report.sources.length > 0 || report.errors.length > 0) && (
        <div className="rounded-lg border border-border bg-muted/20 p-4 text-xs text-muted-foreground">
          {report.sources.length > 0 && (
            <p>
              <span className="font-medium">Sources:</span>{" "}
              {report.sources.join(", ")}
            </p>
          )}
          {report.errors.length > 0 && (
            <p className="mt-1 text-verdict-sell">
              <span className="font-medium">Warnings:</span>{" "}
              {report.errors.join("; ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
