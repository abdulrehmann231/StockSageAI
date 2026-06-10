"""Alerts API (plan § 4.11).

User-owned trigger rules. The engine that fires them lives in
``workers/alert_engine.py``; this router only handles CRUD.

``condition`` is validated here per ``alert_type`` so a malformed rule can't
be stored. The engine takes the validated shape on trust.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from core.deps import CurrentUser, DbSession
from db.models import Alert, Stock
from db.schemas import (
    AlertCreateRequest,
    AlertOut,
    AlertUpdateRequest,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
async def list_alerts(user: CurrentUser, db: DbSession) -> list[AlertOut]:
    rows = (
        await db.scalars(
            select(Alert)
            .where(Alert.user_id == user.id)
            .order_by(Alert.created_at.desc())
        )
    ).all()
    return [AlertOut.model_validate(row) for row in rows]


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
async def create_alert(
    payload: AlertCreateRequest,
    user: CurrentUser,
    db: DbSession,
) -> AlertOut:
    ticker = payload.ticker.strip().upper()
    stock = await db.scalar(select(Stock).where(Stock.ticker == ticker))
    if not stock:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Stock {ticker} not found")

    _validate_condition(payload.alert_type, payload.condition)

    alert = Alert(
        user_id=user.id,
        ticker=ticker,
        alert_type=payload.alert_type,
        condition=payload.condition,
        cooldown_hours=payload.cooldown_hours,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return AlertOut.model_validate(alert)


@router.patch("/{alert_id}", response_model=AlertOut)
async def update_alert(
    alert_id: uuid.UUID,
    payload: AlertUpdateRequest,
    user: CurrentUser,
    db: DbSession,
) -> AlertOut:
    alert = await db.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id)
    )
    if not alert:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")

    if payload.condition is not None:
        _validate_condition(alert.alert_type, payload.condition)
        alert.condition = payload.condition
    if payload.is_active is not None:
        alert.is_active = payload.is_active
    if payload.cooldown_hours is not None:
        alert.cooldown_hours = payload.cooldown_hours
    await db.commit()
    await db.refresh(alert)
    return AlertOut.model_validate(alert)


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    alert = await db.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id)
    )
    if not alert:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    await db.delete(alert)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Condition validation
# --------------------------------------------------------------------------- #


def _validate_condition(alert_type: str, condition: dict[str, Any]) -> None:
    """Raise 422 if ``condition`` doesn't match the schema for ``alert_type``."""
    if alert_type == "PRICE_DROP":
        threshold = condition.get("threshold_pct")
        if not isinstance(threshold, (int, float)) or threshold >= 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "PRICE_DROP requires {'threshold_pct': <negative number>}",
            )
    elif alert_type == "PRICE_RISE":
        threshold = condition.get("threshold_pct")
        if not isinstance(threshold, (int, float)) or threshold <= 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "PRICE_RISE requires {'threshold_pct': <positive number>}",
            )
    elif alert_type == "PRICE_TARGET":
        target = condition.get("target")
        direction = condition.get("direction")
        if not isinstance(target, (int, float)) or target <= 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "PRICE_TARGET requires {'target': <positive number>, 'direction': 'above'|'below'}",
            )
        if direction not in ("above", "below"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "PRICE_TARGET 'direction' must be 'above' or 'below'",
            )
    elif alert_type == "BIG_NEWS":
        impacts = condition.get("impacts") or ["HIGH_POSITIVE", "HIGH_NEGATIVE"]
        if not isinstance(impacts, list) or not all(
            i in ("HIGH_POSITIVE", "HIGH_NEGATIVE", "MEDIUM_POSITIVE", "MEDIUM_NEGATIVE")
            for i in impacts
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "BIG_NEWS 'impacts' must be a list of NewsImpact values",
            )
    elif alert_type == "SENTIMENT_SHIFT":
        from_label = condition.get("from")
        to_label = condition.get("to")
        if from_label not in (None, "bullish", "bearish", "neutral") or to_label not in (
            "bullish",
            "bearish",
            "neutral",
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "SENTIMENT_SHIFT requires {'to': 'bullish'|'bearish'|'neutral'} "
                "and optionally {'from': ...}",
            )
    else:  # pragma: no cover - DB CHECK constraint already excludes other types
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown alert_type {alert_type}",
        )
