"""Portfolio Analyst Agent — the 6th agent (plan § 4.15.4).

Given a user's enriched holdings + portfolio metrics + risk profile, produce
holistic, actionable portfolio advice: a 0-100 health score, strengths,
weaknesses, concrete recommendations, tax-loss-harvesting opportunities, and
concentration warnings.

Mirrors the Report Writer's two-path design:

1. **Deterministic path** (always available, fully offline): scores health from
   concentration, diversification, performance, and risk-profile match, and
   derives recommendations directly from the holdings. Pure functions → testable
   without a network or an LLM.
2. **LLM path** (preferred when ``OPENROUTER_API_KEY`` is set): hands the
   condensed portfolio summary to ``llm_service.analyze_portfolio`` for nuanced
   narrative. Output is validated/clamped; anything unusable falls back to the
   deterministic result.
"""

from __future__ import annotations

import logging
from typing import Any

from db.schemas import HoldingOut, PortfolioMetrics
from services import llm_service

logger = logging.getLogger(__name__)

CONCENTRATION_LIMIT_PCT = 25.0  # single holding above this is a warning
SECTOR_CONCENTRATION_LIMIT_PCT = 40.0

# Target equity-position spread by declared risk tolerance — used only to nudge
# the health score, never as a hard rule.
_RISK_MIN_HOLDINGS = {
    "Conservative": 8,
    "Moderate": 5,
    "Aggressive": 3,
}


async def analyze_portfolio(
    holdings: list[HoldingOut],
    metrics: PortfolioMetrics,
    *,
    risk_profile: str | None = None,
    individual_reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce a structured portfolio analysis dict.

    Shape::

        {
            "health_score": 72,
            "summary": "...",
            "strengths": [...],
            "weaknesses": [...],
            "recommendations": [...],
            "tax_loss_opportunities": [...],
            "concentration_warnings": [...],
            "model_used": "..." | None,
        }
    """
    weights = _position_weights(holdings, metrics.total_value)
    deterministic = _deterministic_analysis(
        holdings, metrics, weights, risk_profile=risk_profile
    )

    llm_payload = await _maybe_llm(
        holdings, metrics, weights, risk_profile, individual_reports, deterministic
    )
    if llm_payload is not None:
        return {**deterministic, **llm_payload}
    return deterministic


# --------------------------------------------------------------------------- #
# Deterministic analysis
# --------------------------------------------------------------------------- #


def _position_weights(holdings: list[HoldingOut], total_value: float) -> dict[str, float]:
    """Map ticker → % of (priced) portfolio value."""
    if total_value <= 0:
        return {}
    return {
        h.ticker: round((h.current_value or 0.0) / total_value * 100.0, 2)
        for h in holdings
        if h.current_value is not None
    }


def _deterministic_analysis(
    holdings: list[HoldingOut],
    metrics: PortfolioMetrics,
    weights: dict[str, float],
    *,
    risk_profile: str | None,
) -> dict[str, Any]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    recommendations: list[str] = []
    concentration_warnings: list[str] = []
    tax_loss_opportunities: list[str] = []

    score = 100.0

    # --- Concentration (single holding) ---
    for ticker, pct in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
        if pct > CONCENTRATION_LIMIT_PCT:
            over = pct - CONCENTRATION_LIMIT_PCT
            score -= min(25.0, over * 0.8)
            concentration_warnings.append(
                f"{ticker} is {pct:.1f}% of the portfolio (above the "
                f"{CONCENTRATION_LIMIT_PCT:.0f}% single-position guideline)."
            )
            recommendations.append(
                f"Consider trimming {ticker} — at {pct:.1f}% it dominates the portfolio."
            )

    # --- Sector concentration ---
    for sector, pct in metrics.sector_allocation.items():
        if pct > SECTOR_CONCENTRATION_LIMIT_PCT:
            score -= min(15.0, (pct - SECTOR_CONCENTRATION_LIMIT_PCT) * 0.5)
            concentration_warnings.append(
                f"{pct:.1f}% of the portfolio sits in {sector} — sector concentration risk."
            )

    # --- Diversification ---
    n_priced = metrics.priced_count
    target = _RISK_MIN_HOLDINGS.get((risk_profile or "Moderate").title(), 5)
    if n_priced == 0:
        score -= 10.0
        weaknesses.append("No holdings could be priced — portfolio health is unknown.")
    elif n_priced < target:
        score -= min(15.0, (target - n_priced) * 3.0)
        weaknesses.append(
            f"Only {n_priced} priced position(s); a {risk_profile or 'Moderate'} "
            f"profile is usually better diversified (~{target}+)."
        )
        recommendations.append(
            "Add a few more positions across different sectors to reduce single-name risk."
        )
    else:
        strengths.append(f"Reasonably diversified across {n_priced} positions.")

    n_sectors = len([s for s in metrics.sector_allocation if s != "Unknown"])
    if n_sectors >= 4:
        strengths.append(f"Exposure spread across {n_sectors} sectors.")

    # --- Performance ---
    pnl_pct = metrics.total_gain_loss_pct
    if pnl_pct >= 10:
        strengths.append(f"Portfolio is up {pnl_pct:.1f}% on cost basis.")
        score += 3.0
    elif pnl_pct <= -10:
        weaknesses.append(f"Portfolio is down {pnl_pct:.1f}% on cost basis.")
        score -= min(10.0, abs(pnl_pct) * 0.2)

    # --- Tax-loss harvesting + delisting ---
    for h in holdings:
        if h.gain_loss is not None and h.gain_loss < 0 and h.cost_basis > 0:
            loss_pct = h.gain_loss / h.cost_basis * 100.0
            if loss_pct <= -5:
                tax_loss_opportunities.append(
                    f"{h.ticker} is down {loss_pct:.1f}% — candidate for tax-loss harvesting."
                )
        if h.is_delisted:
            weaknesses.append(f"{h.ticker} is flagged delisted — review/exit this position.")
            recommendations.append(f"Investigate {h.ticker}: marked delisted upstream.")

    # --- Market split (PSX vs Global) ---
    market_alloc = metrics.market_allocation
    if market_alloc and len(market_alloc) == 1:
        only = next(iter(market_alloc))
        recommendations.append(
            f"All exposure is in one market ({only}); consider geographic diversification."
        )

    score = max(0, min(100, round(score)))

    summary = _deterministic_summary(metrics, score, risk_profile)

    # De-dup + cap lists for a tidy payload.
    return {
        "health_score": int(score),
        "summary": summary,
        "strengths": _dedupe(strengths)[:5],
        "weaknesses": _dedupe(weaknesses)[:5],
        "recommendations": _dedupe(recommendations)[:5],
        "tax_loss_opportunities": _dedupe(tax_loss_opportunities)[:5],
        "concentration_warnings": _dedupe(concentration_warnings)[:5],
        "model_used": None,
    }


def _deterministic_summary(
    metrics: PortfolioMetrics, score: int, risk_profile: str | None
) -> str:
    profile = risk_profile or "unspecified-risk"
    return (
        f"Portfolio health score: {score}/100 ({profile} profile). "
        f"{metrics.priced_count} of {metrics.holdings_count} positions priced, "
        f"total value {metrics.total_value:,.2f} on a cost basis of "
        f"{metrics.total_cost_basis:,.2f} "
        f"({metrics.total_gain_loss_pct:+.1f}%)."
    )


# --------------------------------------------------------------------------- #
# LLM path
# --------------------------------------------------------------------------- #


async def _maybe_llm(
    holdings: list[HoldingOut],
    metrics: PortfolioMetrics,
    weights: dict[str, float],
    risk_profile: str | None,
    individual_reports: list[dict[str, Any]] | None,
    deterministic: dict[str, Any],
) -> dict[str, Any] | None:
    payload = {
        "risk_profile": risk_profile,
        "metrics": metrics.model_dump(mode="json"),
        "position_weights_pct": weights,
        "holdings": [
            {
                "ticker": h.ticker,
                "sector": h.sector,
                "market": h.market,
                "quantity": h.quantity,
                "gain_loss_pct": h.gain_loss_pct,
                "is_delisted": h.is_delisted,
            }
            for h in holdings
        ],
        "individual_reports": [
            {
                "ticker": r.get("ticker"),
                "verdict": r.get("verdict"),
                "composite_score": r.get("composite_score"),
            }
            for r in (individual_reports or [])
        ],
        "suggested_health_score": deterministic["health_score"],
    }

    response = await llm_service.analyze_portfolio(payload=payload)
    if not isinstance(response, dict):
        return None

    summary = _clean_text(response.get("summary"))
    if not summary:
        logger.info("Portfolio LLM omitted summary; using deterministic analysis")
        return None

    out: dict[str, Any] = {
        "summary": summary,
        "strengths": _clean_list(response.get("strengths")) or deterministic["strengths"],
        "weaknesses": _clean_list(response.get("weaknesses")) or deterministic["weaknesses"],
        "recommendations": (
            _clean_list(response.get("recommendations")) or deterministic["recommendations"]
        ),
        "model_used": response.get("_model"),
    }
    score = response.get("health_score")
    if isinstance(score, (int, float)):
        out["health_score"] = int(max(0, min(100, round(float(score)))))
    # Tax-loss + concentration come from real holdings data; keep deterministic.
    out["tax_loss_opportunities"] = deterministic["tax_loss_opportunities"]
    out["concentration_warnings"] = deterministic["concentration_warnings"]
    return out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _clean_list(value: Any, *, limit: int = 5) -> list[str]:
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
