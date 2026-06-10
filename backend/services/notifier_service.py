"""Notifier abstraction used by the alert engine.

Production plan calls for email (Resend / SendGrid) and in-app push, but those
need accounts + secrets we deliberately don't ship in tests/scaffolding. The
``Notifier`` protocol below keeps the alert engine clean: today we ship a
``LogNotifier`` that just records each event in-memory + writes a structured
log line. A real email implementation slots in by replacing
``get_default_notifier`` without touching ``workers/alert_engine.py``.
"""

from __future__ import annotations

import logging
from typing import Protocol

from db.schemas import AlertFiredEvent

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    """Minimal contract: deliver one fired alert."""

    async def send(self, event: AlertFiredEvent) -> None:
        ...


class LogNotifier:
    """Default notifier — logs at INFO and keeps a small in-memory ring buffer.

    The ring buffer is purely for tests and for an eventual ``GET /api/alerts/
    history`` endpoint; production would point at a persistent ``briefings``
    row plus a real email/push channel.
    """

    def __init__(self, max_history: int = 200) -> None:
        self._history: list[AlertFiredEvent] = []
        self._max_history = max_history

    async def send(self, event: AlertFiredEvent) -> None:
        logger.info(
            "ALERT FIRED %s (%s) → %s: %s",
            event.ticker,
            event.alert_type,
            event.user_id,
            event.message,
            extra={
                "alert_id": str(event.alert_id),
                "details": event.details,
            },
        )
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

    @property
    def history(self) -> list[AlertFiredEvent]:
        return list(self._history)

    def clear(self) -> None:
        self._history.clear()


_default_notifier: Notifier = LogNotifier()


def get_default_notifier() -> Notifier:
    """Return the process-wide default notifier (mockable in tests)."""
    return _default_notifier


def set_default_notifier(notifier: Notifier) -> None:
    """Swap the process-wide notifier — used by tests and future email impl."""
    global _default_notifier
    _default_notifier = notifier
