"""Alert engine (plan § 4.11).

Two layers:

1. **Evaluators** — pure functions per alert type. Take the current Price /
   News / Sentiment payloads + the alert's stored ``condition`` blob and return
   ``(fired: bool, message: str, details: dict)``. Pure → trivially unit-
   testable offline.

2. **Engine** — ``run_alert_engine(db)``:

   - loads every active alert,
   - groups them by ticker so Price/News/Sentiment are fetched at most once per
     ticker per sweep (cheap when the agents themselves are cached),
   - applies the cooldown gate (``last_triggered`` + ``cooldown_hours``) BEFORE
     calling the underlying agents — a cooled-down alert costs nothing this
     sweep,
   - delegates to the notifier service for delivery,
   - updates ``last_triggered`` so the cooldown sticks across sweeps,
   - returns a ``AlertEngineRunResult`` summary.

The engine is callable from a regular async context — wiring it into Celery
beat is a deployment concern, not an engine concern. Tests exercise the engine
directly via ``run_alert_engine`` with stubbed agents and a fake notifier.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents import news_agent, price_agent, sentiment_agent
from agents.news_agent import NewsResult
from agents.sentiment_agent import SentimentResult
from db.models import Alert, Stock
from db.schemas import AlertEngineRunResult, AlertFiredEvent
from db.session import SessionLocal
from services import notifier_service

logger = logging.getLogger(__name__)

EvalResult = tuple[bool, str, dict[str, Any]]


# --------------------------------------------------------------------------- #
# Evaluators (pure)
# --------------------------------------------------------------------------- #


def evaluate_price_drop(condition: dict[str, Any], price) -> EvalResult:
    """Fires when price.change_pct <= threshold_pct (a negative number)."""
    if price is None or price.change_pct is None:
        return False, "", {}
    threshold = float(condition.get("threshold_pct", -5.0))
    if price.change_pct <= threshold:
        return (
            True,
            f"{price.ticker} is down {abs(price.change_pct):.2f}% "
            f"(threshold {abs(threshold):.2f}%).",
            {"change_pct": price.change_pct, "price": price.price},
        )
    return False, "", {}


def evaluate_price_rise(condition: dict[str, Any], price) -> EvalResult:
    """Fires when price.change_pct >= threshold_pct (a positive number)."""
    if price is None or price.change_pct is None:
        return False, "", {}
    threshold = float(condition.get("threshold_pct", 5.0))
    if price.change_pct >= threshold:
        return (
            True,
            f"{price.ticker} is up {price.change_pct:+.2f}% "
            f"(threshold {threshold:+.2f}%).",
            {"change_pct": price.change_pct, "price": price.price},
        )
    return False, "", {}


def evaluate_price_target(condition: dict[str, Any], price) -> EvalResult:
    """Fires when the current price crosses a target in the configured direction."""
    if price is None:
        return False, "", {}
    target = float(condition.get("target", 0.0))
    direction = condition.get("direction", "above")
    if direction == "above" and price.price >= target:
        return (
            True,
            f"{price.ticker} reached your target — current {price.price:.2f} ≥ {target:.2f}.",
            {"price": price.price, "target": target, "direction": "above"},
        )
    if direction == "below" and price.price <= target:
        return (
            True,
            f"{price.ticker} fell to your target — current {price.price:.2f} ≤ {target:.2f}.",
            {"price": price.price, "target": target, "direction": "below"},
        )
    return False, "", {}


def evaluate_big_news(condition: dict[str, Any], news: NewsResult | None) -> EvalResult:
    """Fires when at least one article matches the configured impact set."""
    if news is None or not news.articles:
        return False, "", {}
    impacts = set(condition.get("impacts") or ["HIGH_POSITIVE", "HIGH_NEGATIVE"])
    for article in news.articles:
        if article.impact.value in impacts:
            return (
                True,
                f"{news.ticker}: {article.impact.value.replace('_', ' ').lower()} — "
                f"{article.title}",
                {
                    "impact": article.impact.value,
                    "headline": article.title,
                    "source": article.source,
                    "url": str(article.url),
                },
            )
    return False, "", {}


def evaluate_sentiment_shift(
    condition: dict[str, Any], sentiment: SentimentResult | None
) -> EvalResult:
    """Fires when the current sentiment label matches the configured ``to`` value.

    If the alert's ``from`` field is also set, the previous label (stored in the
    condition by the engine after a previous trigger) must match too.
    """
    if sentiment is None or sentiment.post_count == 0:
        return False, "", {}
    desired_to = condition.get("to")
    desired_from = condition.get("from")
    last_seen = condition.get("_last_seen_label")  # bookkeeping set by engine
    label = sentiment.label
    if desired_to and label != desired_to:
        return False, "", {}
    if desired_from and last_seen and last_seen != desired_from:
        return False, "", {}
    if desired_from and not last_seen:
        # Need at least one prior observation to establish the "from" state.
        return False, "", {}
    return (
        True,
        f"{sentiment.ticker} sentiment is now {label} "
        f"(score {sentiment.overall_sentiment:+.2f}).",
        {
            "label": label,
            "score": sentiment.overall_sentiment,
            "previous_label": last_seen,
        },
    )


EVALUATORS: dict[str, Callable] = {
    "PRICE_DROP": evaluate_price_drop,
    "PRICE_RISE": evaluate_price_rise,
    "PRICE_TARGET": evaluate_price_target,
    "BIG_NEWS": evaluate_big_news,
    "SENTIMENT_SHIFT": evaluate_sentiment_shift,
}


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


def _is_in_cooldown(alert: Alert, *, now: datetime) -> bool:
    if alert.last_triggered is None or alert.cooldown_hours <= 0:
        return False
    cooldown_until = alert.last_triggered + timedelta(hours=alert.cooldown_hours)
    return now < cooldown_until


async def run_alert_engine(
    db: AsyncSession | None = None,
    *,
    notifier: notifier_service.Notifier | None = None,
    now: datetime | None = None,
) -> AlertEngineRunResult:
    """One sweep over every active alert.

    Pass ``db`` for tests; production callers can omit it and let the engine
    open its own session. ``notifier`` defaults to the process-wide notifier
    (see ``services/notifier_service.get_default_notifier``).
    """
    if notifier is None:
        notifier = notifier_service.get_default_notifier()
    now = now or datetime.now(timezone.utc)

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    fired: list[AlertFiredEvent] = []
    errors: list[str] = []
    skipped_cooldown = 0
    scanned = 0

    try:
        alerts = (
            await db.scalars(select(Alert).where(Alert.is_active.is_(True)))
        ).all()
        scanned = len(alerts)

        active_by_ticker: dict[tuple[str, str], list[Alert]] = {}
        for alert in alerts:
            if _is_in_cooldown(alert, now=now):
                skipped_cooldown += 1
                continue
            # Look up the stock's market once; we group by (ticker, market).
            stock = await db.scalar(select(Stock).where(Stock.ticker == alert.ticker))
            if not stock:
                errors.append(f"{alert.ticker}: stock row missing, skipping alert {alert.id}")
                continue
            key = (alert.ticker, stock.market)
            active_by_ticker.setdefault(key, []).append(alert)

        for (ticker, market), alerts_for_ticker in active_by_ticker.items():
            try:
                price, news, sentiment = await _fetch_signals_for_ticker(
                    ticker, market, alerts_for_ticker, errors
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ticker}: signal fetch failed: {exc}")
                continue

            for alert in alerts_for_ticker:
                evaluator = EVALUATORS.get(alert.alert_type)
                if evaluator is None:
                    errors.append(
                        f"{ticker}: unknown alert_type {alert.alert_type} (alert {alert.id})"
                    )
                    continue
                try:
                    did_fire, message, details = _apply_evaluator(
                        evaluator, alert, price, news, sentiment
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{ticker}: evaluator failed: {exc}")
                    continue
                # Bookkeep last-seen sentiment label even when the alert didn't
                # fire, so a SENTIMENT_SHIFT can compare against a prior state.
                if alert.alert_type == "SENTIMENT_SHIFT" and sentiment is not None:
                    new_condition = dict(alert.condition or {})
                    new_condition["_last_seen_label"] = sentiment.label
                    alert.condition = new_condition

                if not did_fire:
                    continue

                event = AlertFiredEvent(
                    alert_id=alert.id,
                    user_id=alert.user_id,
                    ticker=alert.ticker,
                    alert_type=alert.alert_type,
                    message=message,
                    details=details,
                    fired_at=now,
                )
                fired.append(event)
                alert.last_triggered = now

                try:
                    await notifier.send(event)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{ticker}: notifier failed: {exc}")

        if fired or any(a.alert_type == "SENTIMENT_SHIFT" for a in alerts):
            await db.commit()
    finally:
        if owns_session:
            await db.close()

    return AlertEngineRunResult(
        scanned=scanned,
        fired=fired,
        errors=errors,
        skipped_cooldown=skipped_cooldown,
    )


async def _fetch_signals_for_ticker(
    ticker: str,
    market: str,
    alerts: Iterable[Alert],
    errors: list[str],
):
    """Run only the agents needed for this ticker's active alerts."""
    types = {a.alert_type for a in alerts}
    needs_price = bool(
        types & {"PRICE_DROP", "PRICE_RISE", "PRICE_TARGET"}
    )
    needs_news = "BIG_NEWS" in types
    needs_sentiment = "SENTIMENT_SHIFT" in types

    async def _maybe_price():
        if not needs_price:
            return None
        try:
            return await price_agent.get_price(ticker, market)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ticker}: price fetch failed: {exc}")
            return None

    async def _maybe_news():
        if not needs_news:
            return None
        try:
            return await news_agent.get_news(ticker, market)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ticker}: news fetch failed: {exc}")
            return None

    async def _maybe_sentiment():
        if not needs_sentiment:
            return None
        try:
            return await sentiment_agent.get_sentiment(ticker, market)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ticker}: sentiment fetch failed: {exc}")
            return None

    return await asyncio.gather(_maybe_price(), _maybe_news(), _maybe_sentiment())


def _apply_evaluator(
    evaluator: Callable,
    alert: Alert,
    price,
    news: NewsResult | None,
    sentiment: SentimentResult | None,
) -> EvalResult:
    if alert.alert_type in ("PRICE_DROP", "PRICE_RISE", "PRICE_TARGET"):
        return evaluator(alert.condition or {}, price)
    if alert.alert_type == "BIG_NEWS":
        return evaluator(alert.condition or {}, news)
    if alert.alert_type == "SENTIMENT_SHIFT":
        return evaluator(alert.condition or {}, sentiment)
    return False, "", {}


# Re-export the schema names so callers can `from workers.alert_engine import
# AlertFiredEvent` without reaching into db.schemas.
__all__ = [
    "EVALUATORS",
    "AlertFiredEvent",
    "AlertEngineRunResult",
    "evaluate_big_news",
    "evaluate_price_drop",
    "evaluate_price_rise",
    "evaluate_price_target",
    "evaluate_sentiment_shift",
    "run_alert_engine",
]
