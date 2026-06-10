"""Chat-with-stock API (plan § 4.9).

After a user generates a Report, they can ask follow-up questions about it.
The endpoint:

1. Loads the persisted Report row (scoped to the current user — no cross-user
   access),
2. Loads the existing chat history for context,
3. Tries the LLM via ``llm_service.answer_chat_question``,
4. Falls back to a deterministic data-lookup answer when the LLM is
   unavailable. Pure technical questions ("What's the P/E?") are answered
   directly without any LLM call so the feature works offline too.
5. Persists both the user turn and the assistant reply.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from core.deps import CurrentUser, DbSession
from db.models import ChatMessage, Report
from db.schemas import ChatMessageOut, ChatMessageRequest, ChatTurnOut
from services import llm_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/{report_id}/history", response_model=list[ChatMessageOut])
async def get_chat_history(
    report_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
) -> list[ChatMessageOut]:
    """Chronological chat history for ``report_id`` (user-scoped)."""
    report = await _load_user_report(report_id, user.id, db)
    if not report:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")

    stmt = (
        select(ChatMessage)
        .where(ChatMessage.report_id == report_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    rows = (await db.scalars(stmt)).all()
    return [ChatMessageOut.model_validate(row) for row in rows]


@router.post(
    "/{report_id}/message",
    response_model=ChatTurnOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_chat_message(
    report_id: uuid.UUID,
    payload: ChatMessageRequest,
    user: CurrentUser,
    db: DbSession,
) -> ChatTurnOut:
    """Append a user turn, generate an assistant reply, return both messages."""
    report = await _load_user_report(report_id, user.id, db)
    if not report:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")

    question = payload.content.strip()
    if not question:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Message is empty")

    # Postgres' transaction-scoped now() would assign Q + A the same
    # created_at, and the UUID id tiebreaker is random — that makes history
    # ordering nondeterministic for fast turns. Stamp from Python so each row
    # has sub-millisecond ordering.
    user_msg = ChatMessage(
        report_id=report_id,
        role="user",
        content=question,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user_msg)
    await db.flush()

    # Build prior history excluding the message we just inserted (it's already
    # in scope as the prompt's user turn).
    history_stmt = (
        select(ChatMessage)
        .where(ChatMessage.report_id == report_id, ChatMessage.id != user_msg.id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    prior_history = [
        {"role": row.role, "content": row.content}
        for row in (await db.scalars(history_stmt)).all()
    ]

    reply_text = await llm_service.answer_chat_question(
        question=question,
        report_payload=report.report_data,
        history=prior_history,
    )
    if not reply_text:
        reply_text = _deterministic_reply(question, report.report_data)

    assistant_msg = ChatMessage(
        report_id=report_id,
        role="assistant",
        content=reply_text,
        created_at=datetime.now(timezone.utc),
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(assistant_msg)

    return ChatTurnOut(
        user_message=ChatMessageOut.model_validate(user_msg),
        assistant_message=ChatMessageOut.model_validate(assistant_msg),
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _load_user_report(report_id: uuid.UUID, user_id: uuid.UUID, db) -> Report | None:
    return await db.scalar(
        select(Report).where(Report.id == report_id, Report.user_id == user_id)
    )


_NUMERIC_FIELDS = {
    "pe": ("price", "pe_ratio", "P/E"),
    "p/e": ("price", "pe_ratio", "P/E"),
    "price-to-earnings": ("price", "pe_ratio", "P/E"),
    "eps": ("price", "eps", "EPS"),
    "earnings per share": ("price", "eps", "EPS"),
    "dividend": ("price", "dividend_yield", "Dividend yield"),
    "yield": ("price", "dividend_yield", "Dividend yield"),
    "market cap": ("price", "market_cap", "Market cap"),
    "marketcap": ("price", "market_cap", "Market cap"),
    "price": ("price", "price", "Price"),
    "52w high": ("price", "week_52_high", "52-week high"),
    "52-week high": ("price", "week_52_high", "52-week high"),
    "52w low": ("price", "week_52_low", "52-week low"),
    "52-week low": ("price", "week_52_low", "52-week low"),
    "verdict": (None, "verdict", "Verdict"),
    "confidence": (None, "confidence", "Confidence"),
    "composite": (None, "composite_score", "Composite signal score"),
    "score": (None, "composite_score", "Composite signal score"),
    "sentiment": ("sentiment", "label", "Sentiment label"),
    "bullish %": ("sentiment", "bullish_pct", "Bullish share"),
    "bearish %": ("sentiment", "bearish_pct", "Bearish share"),
}


def _deterministic_reply(question: str, report: dict[str, Any]) -> str:
    """Answer common technical questions without calling the LLM.

    Returns a plain-prose summary of the report when the question doesn't match
    a known numeric field — this is the fallback used when the LLM is offline.
    """
    q = question.lower()

    for keyword, (section, field, label) in _NUMERIC_FIELDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", q):
            value = _extract_field(report, section, field)
            if value is not None:
                return f"{label}: {_format_value(value)}."
            return f"{label} is not in the current report for this stock."

    # Generic fallback: collapse the report into a one-liner.
    ticker = report.get("ticker", "the stock")
    verdict = report.get("verdict", "HOLD")
    confidence = report.get("confidence", "low")
    exec_summary = (report.get("executive_summary") or "").strip()
    if exec_summary:
        return f"{verdict} (confidence: {confidence}). {exec_summary}"
    return (
        f"{ticker}: {verdict} verdict at {confidence} confidence. Ask about a "
        f"specific field (price, P/E, EPS, sentiment) for a precise answer."
    )


def _extract_field(
    report: dict[str, Any], section: str | None, field: str
) -> Any:
    if section is None:
        return report.get(field)
    block = report.get(section)
    if not isinstance(block, dict):
        return None
    return block.get(field)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if abs(value) >= 1e9:
            return f"{value / 1e9:.2f}B"
        if abs(value) >= 1e6:
            return f"{value / 1e6:.2f}M"
        return f"{value:.2f}"
    return str(value)
