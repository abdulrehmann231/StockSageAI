"""Portfolio math — P&L enrichment, aggregate metrics, and tax estimation.

Phase 7 (plan § 4.15). The deterministic core of the portfolio tracker. This
module is intentionally free of FastAPI / DB-session coupling: the API layer
loads ``(Holding, Stock)`` rows and hands them here. Price data is fetched via
the existing Price Agent, with per-ticker failure isolation so one dead ticker
never sinks the whole portfolio view.

Tax estimation implements the rules in plan § 4.15.5:

* **Pakistan (PSX):** 15% CGT on stocks held < 12 months, 12.5% if held ≥ 12
  months.
* **US / Global:** short-term gains (< 12 months) taxed as income (a flat
  ``US_SHORT_TERM_RATE`` proxy — the real rate is the user's marginal bracket),
  long-term (≥ 12 months) at 15%.

Only **positive** gains incur tax; a holding sitting on a loss is flagged as a
potential tax-loss-harvesting opportunity instead.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Iterable

from agents import price_agent
from db.models import Holding, Stock
from db.schemas import (
    HoldingOut,
    PortfolioMetrics,
    TaxEstimateOut,
    TaxLotEstimate,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Tax constants (plan § 4.15.5)
# --------------------------------------------------------------------------- #

LONG_TERM_THRESHOLD_DAYS = 365
NEAR_THRESHOLD_WINDOW_DAYS = 30

PSX_SHORT_TERM_RATE = 15.0  # %, held < 12 months
PSX_LONG_TERM_RATE = 12.5  # %, held ≥ 12 months
US_SHORT_TERM_RATE = 22.0  # %, proxy for marginal income rate
US_LONG_TERM_RATE = 15.0  # %, held ≥ 12 months


def _is_psx(market: str | None) -> bool:
    return (market or "").strip().upper() == "PSX"


# --------------------------------------------------------------------------- #
# Price fetch (isolated)
# --------------------------------------------------------------------------- #


async def _fetch_prices(
    rows: list[tuple[Holding, Stock]],
    *,
    use_cache: bool,
) -> dict[str, Any]:
    """Fetch a price per distinct ticker concurrently, isolating failures.

    Returns a dict ``{ticker: PriceQuote | Exception}`` so callers can decide
    how to surface a missing price per-holding.
    """
    distinct: dict[str, str] = {}
    for holding, stock in rows:
        distinct.setdefault(holding.ticker, stock.market)

    async def _one(ticker: str, market: str) -> Any:
        try:
            return await price_agent.get_price(ticker, market, use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001
            logger.info("Portfolio price fetch failed for %s: %s", ticker, exc)
            return exc

    tickers = list(distinct.items())
    results = await asyncio.gather(*(_one(t, m) for t, m in tickers))
    return {ticker: result for (ticker, _), result in zip(tickers, results)}


# --------------------------------------------------------------------------- #
# Holding enrichment
# --------------------------------------------------------------------------- #


def _enrich_one(holding: Holding, stock: Stock, price: Any) -> HoldingOut:
    """Build a :class:`HoldingOut` from a holding row + its (maybe failed) price."""
    quantity = float(holding.quantity)
    avg_buy_price = float(holding.avg_buy_price)
    cost_basis = quantity * avg_buy_price

    out = HoldingOut(
        id=holding.id,
        ticker=holding.ticker,
        name=stock.name,
        market=stock.market,
        sector=stock.sector,
        currency=stock.currency,
        quantity=quantity,
        avg_buy_price=avg_buy_price,
        buy_date=holding.buy_date,
        notes=holding.notes,
        is_active=holding.is_active,
        cost_basis=round(cost_basis, 4),
    )

    if isinstance(price, Exception) or price is None:
        out.price_error = (
            f"{type(price).__name__}: {price}" if isinstance(price, Exception) else "no price"
        )
        return out

    current_price = float(price.price)
    current_value = quantity * current_price
    gain_loss = current_value - cost_basis
    gain_loss_pct = (gain_loss / cost_basis * 100.0) if cost_basis else 0.0

    out.current_price = round(current_price, 4)
    out.current_value = round(current_value, 4)
    out.gain_loss = round(gain_loss, 4)
    out.gain_loss_pct = round(gain_loss_pct, 4)
    out.is_delisted = bool(getattr(price, "is_delisted", False))
    return out


def compute_metrics(holdings: list[HoldingOut]) -> PortfolioMetrics:
    """Aggregate enriched holdings into portfolio-wide metrics (pure function)."""
    metrics = PortfolioMetrics(holdings_count=len(holdings))
    if not holdings:
        return metrics

    total_value = 0.0
    total_cost = 0.0
    day_change = 0.0
    priced = 0
    sector_value: dict[str, float] = {}
    market_value: dict[str, float] = {}

    best: HoldingOut | None = None
    worst: HoldingOut | None = None

    for h in holdings:
        total_cost += h.cost_basis
        if h.current_value is None:
            continue
        priced += 1
        total_value += h.current_value

        sector = h.sector or "Unknown"
        sector_value[sector] = sector_value.get(sector, 0.0) + h.current_value
        market = h.market or "Unknown"
        market_value[market] = market_value.get(market, 0.0) + h.current_value

        if h.gain_loss_pct is not None:
            if best is None or h.gain_loss_pct > (best.gain_loss_pct or float("-inf")):
                best = h
            if worst is None or h.gain_loss_pct < (worst.gain_loss_pct or float("inf")):
                worst = h

    total_gain = total_value - sum(
        h.cost_basis for h in holdings if h.current_value is not None
    )

    metrics.priced_count = priced
    metrics.total_value = round(total_value, 4)
    metrics.total_cost_basis = round(total_cost, 4)
    metrics.total_gain_loss = round(total_gain, 4)
    priced_cost = sum(h.cost_basis for h in holdings if h.current_value is not None)
    metrics.total_gain_loss_pct = round(
        (total_gain / priced_cost * 100.0) if priced_cost else 0.0, 4
    )
    metrics.day_change = round(day_change, 4)

    if best is not None:
        metrics.best_performer = _performer(best)
    if worst is not None:
        metrics.worst_performer = _performer(worst)

    metrics.sector_allocation = _to_pct(sector_value, total_value)
    metrics.market_allocation = _to_pct(market_value, total_value)
    return metrics


def _performer(h: HoldingOut) -> dict[str, Any]:
    return {
        "ticker": h.ticker,
        "name": h.name,
        "gain_loss_pct": h.gain_loss_pct,
        "gain_loss": h.gain_loss,
    }


def _to_pct(values: dict[str, float], total: float) -> dict[str, float]:
    if total <= 0:
        return {}
    return {key: round(val / total * 100.0, 2) for key, val in sorted(values.items())}


async def build_portfolio(
    rows: list[tuple[Holding, Stock]],
    *,
    use_cache: bool = True,
) -> tuple[list[HoldingOut], PortfolioMetrics, list[str]]:
    """Enrich active holdings with live P&L and compute aggregate metrics."""
    if not rows:
        return [], PortfolioMetrics(), []

    prices = await _fetch_prices(rows, use_cache=use_cache)
    holdings = [_enrich_one(h, s, prices.get(h.ticker)) for h, s in rows]
    errors = [
        f"{h.ticker}: {h.price_error}" for h in holdings if h.price_error is not None
    ]
    metrics = compute_metrics(holdings)
    return holdings, metrics, errors


# --------------------------------------------------------------------------- #
# Tax estimation
# --------------------------------------------------------------------------- #


def _holding_period_days(buy_date: date | None, today: date) -> int | None:
    if buy_date is None:
        return None
    return (today - buy_date).days


def tax_rate_for(market: str, is_long_term: bool) -> float:
    if _is_psx(market):
        return PSX_LONG_TERM_RATE if is_long_term else PSX_SHORT_TERM_RATE
    return US_LONG_TERM_RATE if is_long_term else US_SHORT_TERM_RATE


def _estimate_lot(holding: Holding, stock: Stock, price: Any, today: date) -> TaxLotEstimate:
    quantity = float(holding.quantity)
    cost_basis = quantity * float(holding.avg_buy_price)
    lot = TaxLotEstimate(
        holding_id=holding.id,
        ticker=holding.ticker,
        market=stock.market,
        quantity=quantity,
        cost_basis=round(cost_basis, 4),
    )

    days = _holding_period_days(holding.buy_date, today)
    lot.holding_period_days = days
    is_long_term = days is not None and days >= LONG_TERM_THRESHOLD_DAYS
    lot.is_long_term = is_long_term if days is not None else None

    if isinstance(price, Exception) or price is None:
        lot.note = "Current price unavailable — tax estimate skipped."
        return lot

    current_value = quantity * float(price.price)
    gain_loss = current_value - cost_basis
    lot.current_value = round(current_value, 4)
    lot.gain_loss = round(gain_loss, 4)

    if gain_loss <= 0:
        lot.tax_rate_pct = 0.0
        lot.estimated_tax = 0.0
        lot.note = "Sitting on a loss — potential tax-loss-harvesting opportunity."
        return lot

    rate = tax_rate_for(stock.market, is_long_term)
    lot.tax_rate_pct = rate
    lot.estimated_tax = round(gain_loss * rate / 100.0, 4)

    # Flag short-term lots that will cross into long-term soon (tax efficiency).
    if days is not None and not is_long_term:
        remaining = LONG_TERM_THRESHOLD_DAYS - days
        if 0 < remaining <= NEAR_THRESHOLD_WINDOW_DAYS:
            lot.near_long_term_threshold = True
            lot.note = (
                f"{remaining} days from the 12-month long-term threshold — "
                "waiting could lower the tax rate."
            )
    return lot


async def estimate_tax(
    rows: list[tuple[Holding, Stock]],
    *,
    today: date | None = None,
    use_cache: bool = True,
) -> TaxEstimateOut:
    """Estimate capital-gains tax liability if all active holdings sold today."""
    today = today or datetime.now(timezone.utc).date()
    out = TaxEstimateOut(fetched_at=datetime.now(timezone.utc))
    if not rows:
        return out

    prices = await _fetch_prices(rows, use_cache=use_cache)
    lots = [_estimate_lot(h, s, prices.get(h.ticker), today) for h, s in rows]
    out.lots = lots
    out.total_estimated_tax = round(
        sum(lot.estimated_tax or 0.0 for lot in lots), 4
    )
    out.total_gain_loss = round(sum(lot.gain_loss or 0.0 for lot in lots), 4)

    markets = {s.market for _, s in rows}
    if any(_is_psx(m) for m in markets) and any(not _is_psx(m) for m in markets):
        out.currency_note = (
            "Portfolio mixes PSX (PKR) and US (USD) holdings — tax totals are "
            "summed in nominal units without FX conversion."
        )
    return out


def snapshot_breakdown(holdings: Iterable[HoldingOut]) -> dict[str, Any]:
    """Per-ticker breakdown blob persisted alongside a daily snapshot."""
    return {
        h.ticker: {
            "quantity": h.quantity,
            "current_value": h.current_value,
            "cost_basis": h.cost_basis,
            "gain_loss": h.gain_loss,
        }
        for h in holdings
    }
