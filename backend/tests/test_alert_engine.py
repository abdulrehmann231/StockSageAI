"""Tests for the Phase-6 alert evaluation engine.

Two layers covered:

1. Pure evaluators — verify each ``evaluate_*`` function over its trigger
   conditions, edge cases, and missing-input handling.

2. ``run_alert_engine`` — end-to-end sweep with stubbed agents and a fake
   notifier, asserting parallel fetch, per-evaluator routing, cooldown gating,
   per-alert error isolation, the sentiment-shift state machine, and that
   ``last_triggered`` is persisted across sweeps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agents import news_agent, price_agent, sentiment_agent
from agents.news_agent import NewsArticle, NewsImpact, NewsResult
from agents.sentiment_agent import SentimentResult
from db.models import Alert, Stock, User
from db.schemas import AlertFiredEvent
from db.session import SessionLocal
from db.schemas import PriceQuote  # noqa: E402  (re-export hop for typing)
from workers import alert_engine
from workers.alert_engine import (
    evaluate_big_news,
    evaluate_price_drop,
    evaluate_price_rise,
    evaluate_price_target,
    evaluate_sentiment_shift,
    run_alert_engine,
)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _price(change_pct: float = -6.0, *, price: float = 145.0):
    from db.schemas import PriceQuote as PQ

    return PQ(
        ticker="AAPL",
        market="GLOBAL",
        currency="USD",
        price=price,
        previous_close=150.0,
        change=price - 150.0,
        change_pct=change_pct,
        fetched_at=datetime.now(timezone.utc),
        source="yfinance",
    )


def _news(impact: NewsImpact = NewsImpact.HIGH_NEGATIVE) -> NewsResult:
    return NewsResult(
        ticker="AAPL",
        market="GLOBAL",
        articles=[
            NewsArticle(
                ticker="AAPL",
                market="GLOBAL",
                title="Apple sued in landmark antitrust case",
                url="https://example.com/x",
                source="Bloomberg",
                published_at=datetime.now(timezone.utc),
                summary="Apple was sued. The case targets the App Store.",
                impact=impact,
                catalysts=["lawsuit"],
                relevance_score=4.0,
            )
        ],
        fetched_at=datetime.now(timezone.utc),
        sources=["Bloomberg"],
    )


def _sentiment(label: str = "bearish", overall: float = -0.6) -> SentimentResult:
    return SentimentResult(
        ticker="AAPL",
        market="GLOBAL",
        overall_sentiment=overall,
        label=label,
        bullish_pct=30,
        bearish_pct=70,
        post_count=25,
        sources=["reddit"],
        fetched_at=datetime.now(timezone.utc),
    )


class _FakeNotifier:
    def __init__(self):
        self.sent: list[AlertFiredEvent] = []
        self.fail_next = False

    async def send(self, event):  # type: ignore[override]
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("notifier broken")
        self.sent.append(event)


# --------------------------------------------------------------------------- #
# Pure evaluators
# --------------------------------------------------------------------------- #


def test_evaluate_price_drop_fires_when_below_threshold():
    fired, msg, details = evaluate_price_drop({"threshold_pct": -5.0}, _price(-6.0))
    assert fired is True
    assert "down 6.00%" in msg
    assert details["change_pct"] == -6.0


def test_evaluate_price_drop_does_not_fire_when_above_threshold():
    fired, *_ = evaluate_price_drop({"threshold_pct": -10.0}, _price(-2.0))
    assert fired is False


def test_evaluate_price_drop_handles_missing_price():
    fired, *_ = evaluate_price_drop({"threshold_pct": -5.0}, None)
    assert fired is False


def test_evaluate_price_rise_mirror():
    fired_up, *_ = evaluate_price_rise({"threshold_pct": 5.0}, _price(7.0))
    fired_flat, *_ = evaluate_price_rise({"threshold_pct": 5.0}, _price(1.0))
    assert fired_up is True
    assert fired_flat is False


@pytest.mark.parametrize(
    "direction,price,fires",
    [
        ("above", 210.0, True),
        ("above", 190.0, False),
        ("below", 190.0, True),
        ("below", 210.0, False),
    ],
)
def test_evaluate_price_target(direction, price, fires):
    fired, *_ = evaluate_price_target(
        {"target": 200.0, "direction": direction}, _price(0.0, price=price)
    )
    assert fired is fires


def test_evaluate_big_news_default_impacts():
    fired, msg, details = evaluate_big_news({}, _news(NewsImpact.HIGH_NEGATIVE))
    assert fired is True
    assert details["impact"] == "HIGH_NEGATIVE"
    assert "antitrust" in msg.lower()


def test_evaluate_big_news_respects_custom_impacts():
    fired, *_ = evaluate_big_news(
        {"impacts": ["HIGH_POSITIVE"]}, _news(NewsImpact.HIGH_NEGATIVE)
    )
    assert fired is False


def test_evaluate_big_news_no_articles():
    empty = NewsResult(
        ticker="AAPL",
        market="GLOBAL",
        articles=[],
        fetched_at=datetime.now(timezone.utc),
    )
    fired, *_ = evaluate_big_news({}, empty)
    assert fired is False


def test_evaluate_sentiment_shift_requires_prior_state_when_from_set():
    """Without a recorded prior label, a ``from``-conditioned alert can't fire."""
    fired, *_ = evaluate_sentiment_shift(
        {"to": "bearish", "from": "bullish"},
        _sentiment("bearish"),
    )
    assert fired is False


def test_evaluate_sentiment_shift_fires_after_transition():
    fired, msg, details = evaluate_sentiment_shift(
        {"to": "bearish", "from": "bullish", "_last_seen_label": "bullish"},
        _sentiment("bearish"),
    )
    assert fired is True
    assert details["previous_label"] == "bullish"
    assert "bearish" in msg


def test_evaluate_sentiment_shift_to_only_fires_immediately():
    fired, *_ = evaluate_sentiment_shift({"to": "bullish"}, _sentiment("bullish", 0.7))
    assert fired is True


# --------------------------------------------------------------------------- #
# End-to-end engine sweeps
# --------------------------------------------------------------------------- #


async def _seed_user_and_stock() -> tuple[User, Stock]:
    async with SessionLocal() as session:
        user = User(email="engine@example.com", password_hash="x")
        stock = Stock(ticker="AAPL", name="Apple Inc.", market="GLOBAL", currency="USD")
        session.add_all([user, stock])
        await session.commit()
        await session.refresh(user)
        await session.refresh(stock)
        return user, stock


def _patch_agents(monkeypatch, *, price=None, news=None, sentiment=None):
    async def _p(ticker, market, **kwargs):
        return price if price is not None else _price()

    async def _n(ticker, market, **kwargs):
        return news if news is not None else _news()

    async def _s(ticker, market, **kwargs):
        return sentiment if sentiment is not None else _sentiment()

    monkeypatch.setattr(price_agent, "get_price", _p)
    monkeypatch.setattr(news_agent, "get_news", _n)
    monkeypatch.setattr(sentiment_agent, "get_sentiment", _s)


@pytest.mark.asyncio
async def test_run_alert_engine_fires_price_drop(monkeypatch):
    user, _stock = await _seed_user_and_stock()
    notifier = _FakeNotifier()
    _patch_agents(monkeypatch, price=_price(-6.0))

    async with SessionLocal() as session:
        session.add(
            Alert(
                user_id=user.id,
                ticker="AAPL",
                alert_type="PRICE_DROP",
                condition={"threshold_pct": -5.0},
                cooldown_hours=24,
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        result = await run_alert_engine(session, notifier=notifier)
    assert result.scanned == 1
    assert len(result.fired) == 1
    assert result.fired[0].alert_type == "PRICE_DROP"
    assert len(notifier.sent) == 1
    assert notifier.sent[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_run_alert_engine_skips_alerts_within_cooldown(monkeypatch):
    user, _stock = await _seed_user_and_stock()
    notifier = _FakeNotifier()
    _patch_agents(monkeypatch, price=_price(-6.0))

    last = datetime.now(timezone.utc) - timedelta(hours=1)
    async with SessionLocal() as session:
        session.add(
            Alert(
                user_id=user.id,
                ticker="AAPL",
                alert_type="PRICE_DROP",
                condition={"threshold_pct": -5.0},
                cooldown_hours=24,
                last_triggered=last,
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        result = await run_alert_engine(session, notifier=notifier)
    assert result.scanned == 1
    assert result.skipped_cooldown == 1
    assert result.fired == []
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_run_alert_engine_persists_last_triggered_across_sweeps(monkeypatch):
    user, _stock = await _seed_user_and_stock()
    notifier = _FakeNotifier()
    _patch_agents(monkeypatch, price=_price(-6.0))

    async with SessionLocal() as session:
        session.add(
            Alert(
                user_id=user.id,
                ticker="AAPL",
                alert_type="PRICE_DROP",
                condition={"threshold_pct": -5.0},
                cooldown_hours=24,
            )
        )
        await session.commit()

    # First sweep fires.
    async with SessionLocal() as session:
        first = await run_alert_engine(session, notifier=notifier)
    assert len(first.fired) == 1

    # Second sweep at the same instant should be gated by cooldown.
    async with SessionLocal() as session:
        second = await run_alert_engine(session, notifier=notifier)
    assert second.skipped_cooldown == 1
    assert second.fired == []


@pytest.mark.asyncio
async def test_run_alert_engine_routes_per_alert_type(monkeypatch):
    """One ticker with three different alerts → each routes to its evaluator."""
    user, _stock = await _seed_user_and_stock()
    notifier = _FakeNotifier()
    _patch_agents(
        monkeypatch,
        price=_price(-6.0),
        news=_news(NewsImpact.HIGH_NEGATIVE),
        sentiment=_sentiment("bearish", -0.7),
    )

    async with SessionLocal() as session:
        session.add_all(
            [
                Alert(
                    user_id=user.id,
                    ticker="AAPL",
                    alert_type="PRICE_DROP",
                    condition={"threshold_pct": -5.0},
                ),
                Alert(
                    user_id=user.id,
                    ticker="AAPL",
                    alert_type="BIG_NEWS",
                    condition={"impacts": ["HIGH_NEGATIVE"]},
                ),
                Alert(
                    user_id=user.id,
                    ticker="AAPL",
                    alert_type="SENTIMENT_SHIFT",
                    condition={"to": "bearish"},
                ),
            ]
        )
        await session.commit()

    async with SessionLocal() as session:
        result = await run_alert_engine(session, notifier=notifier)

    fired_types = sorted(e.alert_type for e in result.fired)
    assert fired_types == ["BIG_NEWS", "PRICE_DROP", "SENTIMENT_SHIFT"]


@pytest.mark.asyncio
async def test_run_alert_engine_isolates_notifier_failures(monkeypatch):
    user, _stock = await _seed_user_and_stock()
    notifier = _FakeNotifier()
    notifier.fail_next = True
    _patch_agents(monkeypatch, price=_price(-6.0))

    async with SessionLocal() as session:
        session.add(
            Alert(
                user_id=user.id,
                ticker="AAPL",
                alert_type="PRICE_DROP",
                condition={"threshold_pct": -5.0},
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        result = await run_alert_engine(session, notifier=notifier)
    # The alert still counts as fired, but the notifier failure is captured.
    assert len(result.fired) == 1
    assert any("notifier failed" in err for err in result.errors)


@pytest.mark.asyncio
async def test_run_alert_engine_sentiment_shift_state_machine(monkeypatch):
    """First sweep records the label; second sweep fires after the transition."""
    user, _stock = await _seed_user_and_stock()
    notifier = _FakeNotifier()

    # Sweep 1: bullish — the engine records last_seen but does not fire because
    # the alert is configured with from=bullish, to=bearish.
    _patch_agents(monkeypatch, sentiment=_sentiment("bullish", 0.5))
    async with SessionLocal() as session:
        session.add(
            Alert(
                user_id=user.id,
                ticker="AAPL",
                alert_type="SENTIMENT_SHIFT",
                condition={"to": "bearish", "from": "bullish"},
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        first = await run_alert_engine(session, notifier=notifier)
    assert first.fired == []

    # Sweep 2: bearish — engine sees the recorded "bullish" and fires.
    _patch_agents(monkeypatch, sentiment=_sentiment("bearish", -0.5))
    async with SessionLocal() as session:
        second = await run_alert_engine(session, notifier=notifier)
    assert len(second.fired) == 1
    assert second.fired[0].alert_type == "SENTIMENT_SHIFT"

    # Stored condition keeps the bookkeeping for the next sweep.
    async with SessionLocal() as session:
        alert = await session.scalar(select(Alert).where(Alert.ticker == "AAPL"))
    assert alert.condition.get("_last_seen_label") == "bearish"


@pytest.mark.asyncio
async def test_run_alert_engine_inactive_alert_not_scanned(monkeypatch):
    user, _stock = await _seed_user_and_stock()
    notifier = _FakeNotifier()
    _patch_agents(monkeypatch, price=_price(-6.0))

    async with SessionLocal() as session:
        session.add(
            Alert(
                user_id=user.id,
                ticker="AAPL",
                alert_type="PRICE_DROP",
                condition={"threshold_pct": -5.0},
                is_active=False,
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        result = await run_alert_engine(session, notifier=notifier)
    assert result.scanned == 0
    assert result.fired == []


@pytest.mark.asyncio
async def test_run_alert_engine_records_agent_failure_without_crashing(monkeypatch):
    user, _stock = await _seed_user_and_stock()
    notifier = _FakeNotifier()

    async def _boom(ticker, market, **kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(price_agent, "get_price", _boom)

    async with SessionLocal() as session:
        session.add(
            Alert(
                user_id=user.id,
                ticker="AAPL",
                alert_type="PRICE_DROP",
                condition={"threshold_pct": -5.0},
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        result = await run_alert_engine(session, notifier=notifier)
    assert result.fired == []
    assert any("upstream down" in err for err in result.errors)


def test_log_notifier_records_history():
    """The default notifier keeps a small in-memory history for visibility."""
    from services.notifier_service import LogNotifier

    notifier = LogNotifier(max_history=2)
    import asyncio
    import uuid

    event = AlertFiredEvent(
        alert_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        ticker="AAPL",
        alert_type="PRICE_DROP",
        message="down 6%",
        details={},
        fired_at=datetime.now(timezone.utc),
    )
    asyncio.run(notifier.send(event))
    asyncio.run(notifier.send(event))
    asyncio.run(notifier.send(event))
    assert len(notifier.history) == 2  # capped


# Keep the unused import for typing consistency with other suites.
_ = PriceQuote
