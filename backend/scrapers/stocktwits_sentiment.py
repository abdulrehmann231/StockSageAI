"""StockTwits sentiment source.

Pulls the most recent messages from StockTwits' free public symbol stream and
normalizes them into the shape consumed by the Sentiment Agent. StockTwits
attaches an explicit Bullish/Bearish label to many messages, which we surface
as ``label`` so the agent can trust the crowd's own tagging instead of guessing
from keywords.

Coverage note: StockTwits is overwhelmingly US/global tickers. PSX symbols are
rarely present, so callers should treat an empty result as normal for PSX.

The function is intentionally defensive — network errors, rate limits (HTTP
429), and unexpected payload shapes all degrade to a raised exception that the
agent isolates per-source, or to an empty list.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

STOCKTWITS_STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
DEFAULT_LIMIT = 30
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _parse_created_at(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    # StockTwits returns ISO-8601 with a trailing Z.
    try:
        cleaned = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None


def _extract_label(message: dict[str, Any]) -> str | None:
    """Read StockTwits' own Bullish/Bearish tag if the author set one."""
    entities = message.get("entities")
    if not isinstance(entities, dict):
        return None
    sentiment = entities.get("sentiment")
    if not isinstance(sentiment, dict):
        return None
    basic = sentiment.get("basic")
    if basic == "Bullish":
        return "bullish"
    if basic == "Bearish":
        return "bearish"
    return None


def _normalize(message: dict[str, Any]) -> dict[str, Any] | None:
    body = (message.get("body") or "").strip()
    if not body:
        return None
    user = message.get("user") or {}
    return {
        "source": "stocktwits",
        "id": f"stocktwits:{message.get('id')}",
        "text": body,
        "created_at": _parse_created_at(message.get("created_at")),
        "author": user.get("username"),
        "url": None,
        "label": _extract_label(message),
        # NOTE: ``score`` is a per-source relevance proxy only, NOT a
        # cross-source-comparable engagement metric. Here it is the author's
        # follower count, whereas reddit_sentiment.py uses the submission's
        # upvote count. The Sentiment Agent uses it to rank top points and
        # should not assume these values share a scale across sources.
        "score": user.get("followers"),
    }


async def fetch_stocktwits_sentiment(
    ticker: str,
    market: str = "GLOBAL",
    company_name: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Fetch recent StockTwits messages for ``ticker``.

    Returns a list of normalized post dicts. Raises on transport/HTTP errors so
    the agent can record a per-source error and continue with other sources.
    """
    symbol = ticker.upper()
    url = STOCKTWITS_STREAM_URL.format(symbol=symbol)
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            # Unknown symbol on StockTwits — common for PSX. Not an error.
            logger.info("StockTwits has no stream for %s", symbol)
            return []
        resp.raise_for_status()
        payload = resp.json()

    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return []

    posts: list[dict[str, Any]] = []
    for message in messages[:limit]:
        if not isinstance(message, dict):
            continue
        normalized = _normalize(message)
        if normalized is not None:
            posts.append(normalized)
    return posts
