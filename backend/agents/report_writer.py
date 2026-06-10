"""Report Writer Agent.

Synthesises the output of the Price, News, and Sentiment agents into a single
``StockReport`` — the deliverable described in plan § 4.8.

Two paths:

1. **LLM path** (preferred when ``OPENROUTER_API_KEY`` is configured): hand the
   condensed signals to ``llm_service.synthesize_report`` and let the model
   produce the verdict, executive summary, and narrative sections. The model
   output is validated, clamped, and backfilled so a malformed/over-eager
   response can't poison the pipeline.

2. **Deterministic path**: always available, runs when the LLM is unavailable or
   returns something unusable. Verdict is derived from a weighted blend of news
   impact, crowd sentiment, and price change; sections are produced from the
   underlying agent payloads. Pure functions so the fallback is fully testable
   offline.

The writer is intentionally tolerant of missing pieces — a ``StockReport`` is
still produced when only Price (or only News + Sentiment) succeeded, with an
``errors`` list explaining what was skipped.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.news_agent import NewsImpact, NewsResult
from agents.sentiment_agent import SentimentResult
from db.schemas import PriceQuote
from services import llm_service

logger = logging.getLogger(__name__)

Verdict = Literal["BUY", "ACCUMULATE", "HOLD", "REDUCE", "SELL"]
ConfidenceLevel = Literal["low", "medium", "high"]


_NEWS_IMPACT_SCORE = {
    NewsImpact.HIGH_POSITIVE: 1.0,
    NewsImpact.MEDIUM_POSITIVE: 0.5,
    NewsImpact.NEUTRAL: 0.0,
    NewsImpact.MEDIUM_NEGATIVE: -0.5,
    NewsImpact.HIGH_NEGATIVE: -1.0,
}


# --------------------------------------------------------------------------- #
# Pydantic schema
# --------------------------------------------------------------------------- #


class StockReport(BaseModel):
    """Final analyst-style report produced by Phase 5."""

    # ``model_used`` would otherwise collide with pydantic's protected ``model_``
    # namespace; disable that protection just for this schema.
    model_config = ConfigDict(protected_namespaces=())

    ticker: str
    market: str
    company_name: str | None = None

    # Top-line verdict.
    verdict: Verdict = "HOLD"
    confidence: ConfidenceLevel = "low"
    composite_score: float = 0.0  # weighted blend, range [-1, 1]

    # Narrative sections — short, grounded in the inputs.
    executive_summary: str
    price_summary: str | None = None
    news_summary: str | None = None
    sentiment_summary: str | None = None

    # Decision aids.
    key_catalysts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)

    # Raw agent payloads for clients that want them.
    price: PriceQuote | None = None
    news: NewsResult | None = None
    sentiment: SentimentResult | None = None

    # Provenance.
    sources: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    model_used: str | None = None  # which LLM path ran, or None for fallback
    fetched_at: datetime
    cached: bool = False


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #


async def write_report(
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    price: PriceQuote | None,
    news: NewsResult | None,
    sentiment: SentimentResult | None,
    errors: list[str] | None = None,
) -> StockReport:
    """Build a :class:`StockReport` from the three agent outputs."""
    ticker = ticker.strip().upper()
    market = market.strip().upper()
    errors = list(errors) if errors else []

    composite, weight = _composite_score(price, news, sentiment)
    base_verdict, base_confidence = _derive_verdict(composite, weight)

    deterministic_summary = _deterministic_executive_summary(
        ticker=ticker,
        market=market,
        company_name=company_name,
        price=price,
        news=news,
        sentiment=sentiment,
        verdict=base_verdict,
        composite=composite,
    )

    llm_payload = await _maybe_llm_synthesis(
        ticker=ticker,
        market=market,
        company_name=company_name,
        price=price,
        news=news,
        sentiment=sentiment,
        composite=composite,
        suggested_verdict=base_verdict,
    )

    if llm_payload is not None:
        executive_summary = llm_payload.get("executive_summary") or deterministic_summary
        verdict = _coerce_verdict(llm_payload.get("verdict"), base_verdict)
        confidence = _coerce_confidence(llm_payload.get("confidence"), base_confidence)
        risks = _clean_string_list(llm_payload.get("risks"), limit=4)
        opportunities = _clean_string_list(llm_payload.get("opportunities"), limit=4)
        price_section = _clean_text(llm_payload.get("price_summary")) or _price_section(price)
        news_section = _clean_text(llm_payload.get("news_summary")) or _news_section(news)
        sentiment_section = (
            _clean_text(llm_payload.get("sentiment_summary")) or _sentiment_section(sentiment)
        )
        model_used = llm_payload.get("_model")
    else:
        executive_summary = deterministic_summary
        verdict = base_verdict
        confidence = base_confidence
        risks = _deterministic_risks(news, sentiment, price)
        opportunities = _deterministic_opportunities(news, sentiment, price)
        price_section = _price_section(price)
        news_section = _news_section(news)
        sentiment_section = _sentiment_section(sentiment)
        model_used = None

    catalysts = _key_catalysts(news)
    sources = _collect_sources(price, news, sentiment)

    return StockReport(
        ticker=ticker,
        market=market,
        company_name=company_name,
        verdict=verdict,
        confidence=confidence,
        composite_score=round(composite, 3),
        executive_summary=executive_summary,
        price_summary=price_section,
        news_summary=news_section,
        sentiment_summary=sentiment_section,
        key_catalysts=catalysts,
        risks=risks,
        opportunities=opportunities,
        price=price,
        news=news,
        sentiment=sentiment,
        sources=sources,
        errors=errors,
        model_used=model_used,
        fetched_at=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Composite scoring + verdict derivation
# --------------------------------------------------------------------------- #


def _composite_score(
    price: PriceQuote | None,
    news: NewsResult | None,
    sentiment: SentimentResult | None,
) -> tuple[float, float]:
    """Blend the three signal channels into a single ``[-1, 1]`` score.

    Returns ``(score, weight)`` where ``weight`` is the total weight of signals
    that contributed (used to derive a confidence level).
    """
    contributions: list[tuple[float, float]] = []

    if news is not None and news.articles:
        # Average impact across the article set, weighted by recency-relevance.
        impact_total = 0.0
        weight_total = 0.0
        for article in news.articles:
            score = _NEWS_IMPACT_SCORE.get(article.impact, 0.0)
            # Relevance score is roughly [0, 6]; clamp to [0.5, 1.5] as a weight.
            weight = max(0.5, min(1.5, (article.relevance_score or 1.0) / 3.0))
            impact_total += score * weight
            weight_total += weight
        if weight_total:
            contributions.append((impact_total / weight_total, 0.45))

    if sentiment is not None and sentiment.post_count > 0:
        # overall_sentiment already in [-1, 1].
        contributions.append((max(-1.0, min(1.0, sentiment.overall_sentiment)), 0.35))

    if price is not None and price.change_pct is not None:
        # Cap at ±10% so a single jumpy day can't dominate the report.
        capped = max(-10.0, min(10.0, price.change_pct))
        contributions.append((capped / 10.0, 0.20))

    if not contributions:
        return 0.0, 0.0

    total_weight = sum(w for _, w in contributions)
    composite = sum(score * w for score, w in contributions) / total_weight
    return composite, total_weight


def _derive_verdict(composite: float, weight: float) -> tuple[Verdict, ConfidenceLevel]:
    """Map a composite score + total signal weight to a verdict + confidence."""
    if composite >= 0.55:
        verdict: Verdict = "BUY"
    elif composite >= 0.20:
        verdict = "ACCUMULATE"
    elif composite <= -0.55:
        verdict = "SELL"
    elif composite <= -0.20:
        verdict = "REDUCE"
    else:
        verdict = "HOLD"

    # weight maxes at ~1.0 (all three channels contributing). Anything under
    # 0.35 means we're guessing off a single signal.
    if weight >= 0.75:
        confidence: ConfidenceLevel = "high"
    elif weight >= 0.40:
        confidence = "medium"
    else:
        confidence = "low"
    return verdict, confidence


# --------------------------------------------------------------------------- #
# Deterministic narrative sections
# --------------------------------------------------------------------------- #


def _deterministic_executive_summary(
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    price: PriceQuote | None,
    news: NewsResult | None,
    sentiment: SentimentResult | None,
    verdict: Verdict,
    composite: float,
) -> str:
    """A two-to-three sentence summary built directly from the agent payloads."""
    subject = company_name or ticker
    parts: list[str] = []

    if price is not None and price.change_pct is not None:
        direction = "up" if price.change_pct >= 0 else "down"
        parts.append(
            f"{subject} ({ticker}, {market}) is trading at "
            f"{_fmt_price(price)} {price.currency or ''}, "
            f"{direction} {abs(price.change_pct):.2f}% on the session."
        )
    else:
        parts.append(f"{subject} ({ticker}, {market}) — live price unavailable.")

    if news is not None and news.articles:
        impact = news.overall_news_sentiment.value.replace("_", " ").lower()
        catalyst = news.top_catalyst or "no single dominant catalyst"
        parts.append(
            f"News flow skews {impact} with {len(news.articles)} relevant article"
            f"{'s' if len(news.articles) != 1 else ''}; "
            f"top catalyst is {catalyst}."
        )
    elif news is not None:
        parts.append("No high-relevance news was found in the recent lookback window.")

    if sentiment is not None and sentiment.post_count > 0:
        parts.append(
            f"Crowd sentiment is {sentiment.label} "
            f"({sentiment.bullish_pct}% bullish / {sentiment.bearish_pct}% bearish "
            f"across {sentiment.post_count} posts)."
        )

    parts.append(f"Overall read: {verdict} (composite signal score {composite:+.2f}).")
    return " ".join(parts).strip()


def _price_section(price: PriceQuote | None) -> str | None:
    if price is None:
        return None
    bits = [
        f"Price: {_fmt_price(price)} {price.currency or ''}".strip(),
    ]
    if price.change is not None and price.change_pct is not None:
        bits.append(f"Change: {price.change:+.2f} ({price.change_pct:+.2f}%)")
    if price.day_high is not None and price.day_low is not None:
        bits.append(f"Day range: {price.day_low:.2f} – {price.day_high:.2f}")
    if price.week_52_high is not None and price.week_52_low is not None:
        bits.append(
            f"52-week range: {price.week_52_low:.2f} – {price.week_52_high:.2f}"
        )
    if price.market_cap:
        bits.append(f"Market cap: {_fmt_large(price.market_cap)}")
    if price.pe_ratio:
        bits.append(f"P/E: {price.pe_ratio:.2f}")
    if price.is_delisted:
        bits.append("Note: ticker is flagged as DELISTED upstream.")
    return ". ".join(bits) + "."


def _news_section(news: NewsResult | None) -> str | None:
    if news is None or not news.articles:
        return None
    lines = [
        f"Overall news tone: {news.overall_news_sentiment.value.replace('_', ' ').lower()}."
    ]
    if news.top_catalyst:
        lines.append(f"Top catalyst: {news.top_catalyst}.")
    for article in news.articles[:3]:
        published = article.published_at.isoformat() if article.published_at else "n/a"
        lines.append(
            f"- [{article.source}, {published}] {article.title} — "
            f"{article.impact.value.replace('_', ' ').lower()}: {article.summary}"
        )
    return "\n".join(lines)


def _sentiment_section(sentiment: SentimentResult | None) -> str | None:
    if sentiment is None or sentiment.post_count == 0:
        return None
    bits = [
        f"Label: {sentiment.label}.",
        f"Score: {sentiment.overall_sentiment:+.2f} on a -1..+1 scale.",
        f"Split: {sentiment.bullish_pct}% bullish / {sentiment.bearish_pct}% bearish "
        f"across {sentiment.post_count} posts.",
    ]
    if sentiment.top_bullish_points:
        bits.append(
            "Bullish themes: " + "; ".join(sentiment.top_bullish_points[:3])
        )
    if sentiment.top_bearish_points:
        bits.append(
            "Bearish themes: " + "; ".join(sentiment.top_bearish_points[:3])
        )
    return " ".join(bits)


def _key_catalysts(news: NewsResult | None) -> list[str]:
    if news is None:
        return []
    seen: list[str] = []
    for article in news.articles:
        for catalyst in article.catalysts:
            if catalyst not in seen:
                seen.append(catalyst)
    return seen[:5]


def _deterministic_risks(
    news: NewsResult | None,
    sentiment: SentimentResult | None,
    price: PriceQuote | None,
) -> list[str]:
    """Concrete-first ordering — a 5% drawdown or DELISTED flag outranks
    article #3 in the negative-news list. We then de-dupe and cap at 4."""
    risks: list[str] = []
    if price is not None and price.is_delisted:
        risks.append("Upstream flagged the ticker as DELISTED.")
    if price is not None and price.change_pct is not None and price.change_pct <= -3.0:
        risks.append(f"Recent price drawdown of {price.change_pct:.2f}%.")
    if news is not None:
        for article in news.articles:
            if article.impact in (NewsImpact.HIGH_NEGATIVE, NewsImpact.MEDIUM_NEGATIVE):
                risks.append(f"{article.source}: {article.title}")
    if sentiment is not None and sentiment.top_bearish_points:
        risks.extend(sentiment.top_bearish_points[:3])
    return _dedupe_strings(risks)[:4]


def _deterministic_opportunities(
    news: NewsResult | None,
    sentiment: SentimentResult | None,
    price: PriceQuote | None,
) -> list[str]:
    """Mirror of :func:`_deterministic_risks` — concrete price strength first."""
    opps: list[str] = []
    if price is not None and price.change_pct is not None and price.change_pct >= 3.0:
        opps.append(f"Recent price strength of {price.change_pct:+.2f}%.")
    if news is not None:
        for article in news.articles:
            if article.impact in (NewsImpact.HIGH_POSITIVE, NewsImpact.MEDIUM_POSITIVE):
                opps.append(f"{article.source}: {article.title}")
    if sentiment is not None and sentiment.top_bullish_points:
        opps.extend(sentiment.top_bullish_points[:3])
    return _dedupe_strings(opps)[:4]


# --------------------------------------------------------------------------- #
# LLM synthesis
# --------------------------------------------------------------------------- #


async def _maybe_llm_synthesis(
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    price: PriceQuote | None,
    news: NewsResult | None,
    sentiment: SentimentResult | None,
    composite: float,
    suggested_verdict: Verdict,
) -> dict[str, Any] | None:
    """Try the LLM synthesis path; return ``None`` to fall back to deterministic."""
    payload = _llm_input_payload(
        ticker=ticker,
        market=market,
        company_name=company_name,
        price=price,
        news=news,
        sentiment=sentiment,
        composite=composite,
        suggested_verdict=suggested_verdict,
    )

    response = await llm_service.synthesize_report(payload=payload)
    if response is None:
        return None

    # Defensive normalisation: model can emit junk and we must not crash.
    if not isinstance(response, dict):
        logger.info("Report LLM returned non-dict payload; falling back")
        return None

    verdict = response.get("verdict")
    confidence = response.get("confidence")
    summary = _clean_text(response.get("executive_summary"))
    if not summary:
        # An LLM response with no exec summary is useless; deterministic wins.
        logger.info("Report LLM omitted executive_summary; falling back")
        return None

    return {
        "verdict": verdict,
        "confidence": confidence,
        "executive_summary": summary,
        "risks": response.get("risks"),
        "opportunities": response.get("opportunities"),
        "price_summary": response.get("price_summary"),
        "news_summary": response.get("news_summary"),
        "sentiment_summary": response.get("sentiment_summary"),
        "_model": response.get("_model"),
    }


def _llm_input_payload(
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    price: PriceQuote | None,
    news: NewsResult | None,
    sentiment: SentimentResult | None,
    composite: float,
    suggested_verdict: Verdict,
) -> dict[str, Any]:
    """Condense agent outputs into a small JSON-safe dict for the model."""
    price_block: dict[str, Any] | None = None
    if price is not None:
        price_block = {
            "currency": price.currency,
            "price": price.price,
            "previous_close": price.previous_close,
            "change": price.change,
            "change_pct": price.change_pct,
            "day_range": [price.day_low, price.day_high],
            "week_52_range": [price.week_52_low, price.week_52_high],
            "market_cap": price.market_cap,
            "pe_ratio": price.pe_ratio,
            "eps": price.eps,
            "dividend_yield": price.dividend_yield,
            "is_delisted": price.is_delisted,
        }

    news_block: dict[str, Any] | None = None
    if news is not None:
        news_block = {
            "overall_tone": news.overall_news_sentiment.value,
            "top_catalyst": news.top_catalyst,
            "sources": news.sources,
            "articles": [
                {
                    "title": article.title,
                    "source": article.source,
                    "published_at": article.published_at.isoformat()
                    if article.published_at
                    else None,
                    "impact": article.impact.value,
                    "catalysts": article.catalysts,
                    "summary": article.summary,
                }
                for article in news.articles[:5]
            ],
        }

    sentiment_block: dict[str, Any] | None = None
    if sentiment is not None:
        sentiment_block = {
            "score": sentiment.overall_sentiment,
            "label": sentiment.label,
            "bullish_pct": sentiment.bullish_pct,
            "bearish_pct": sentiment.bearish_pct,
            "post_count": sentiment.post_count,
            "sources": sentiment.sources,
            "top_bullish_points": sentiment.top_bullish_points[:3],
            "top_bearish_points": sentiment.top_bearish_points[:3],
        }

    return {
        "ticker": ticker,
        "market": market,
        "company_name": company_name,
        "composite_score": round(composite, 3),
        "suggested_verdict": suggested_verdict,
        "price": price_block,
        "news": news_block,
        "sentiment": sentiment_block,
    }


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


def _coerce_verdict(value: Any, fallback: Verdict) -> Verdict:
    if not isinstance(value, str):
        return fallback
    upper = value.strip().upper()
    aliases = {
        "STRONG_BUY": "BUY",
        "STRONG BUY": "BUY",
        "OUTPERFORM": "ACCUMULATE",
        "OVERWEIGHT": "ACCUMULATE",
        "NEUTRAL": "HOLD",
        "MARKET PERFORM": "HOLD",
        "UNDERPERFORM": "REDUCE",
        "UNDERWEIGHT": "REDUCE",
        "STRONG_SELL": "SELL",
        "STRONG SELL": "SELL",
    }
    upper = aliases.get(upper, upper)
    if upper in ("BUY", "ACCUMULATE", "HOLD", "REDUCE", "SELL"):
        return upper  # type: ignore[return-value]
    return fallback


def _coerce_confidence(value: Any, fallback: ConfidenceLevel) -> ConfidenceLevel:
    if not isinstance(value, str):
        return fallback
    lower = value.strip().lower()
    if lower in ("low", "medium", "high"):
        return lower  # type: ignore[return-value]
    if lower in ("med", "moderate"):
        return "medium"
    return fallback


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    # Collapse paragraph-internal whitespace but keep newlines intact for
    # multi-line LLM responses.
    return " ".join(line.strip() for line in text.splitlines() if line.strip()) if "\n" not in text else text


def _clean_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _collect_sources(
    price: PriceQuote | None,
    news: NewsResult | None,
    sentiment: SentimentResult | None,
) -> list[str]:
    sources: list[str] = []
    if price is not None:
        sources.append(price.source)
    if news is not None:
        sources.extend(news.sources)
    if sentiment is not None:
        sources.extend(sentiment.sources)
    return _dedupe_strings(sources)


def _fmt_price(price: PriceQuote) -> str:
    if price.price >= 1000:
        return f"{price.price:,.2f}"
    return f"{price.price:.2f}"


def _fmt_large(value: float) -> str:
    abs_v = abs(value)
    if abs_v >= 1e12:
        return f"{value / 1e12:.2f}T"
    if abs_v >= 1e9:
        return f"{value / 1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{value / 1e6:.2f}M"
    return f"{value:,.0f}"
