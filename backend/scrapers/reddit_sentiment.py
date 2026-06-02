"""Reddit sentiment source via PRAW (read-only).

Searches the relevant investing subreddits for a ticker / company name over the
last month and normalizes submissions into the Sentiment Agent's post shape.

PRAW is a synchronous client, so the blocking work runs inside
``asyncio.to_thread``. Credentials are optional: when ``REDDIT_CLIENT_ID`` /
``REDDIT_CLIENT_SECRET`` are absent the fetcher returns an empty list instead of
raising, so the agent simply falls back to its other sources.

Subreddit routing:
- Global tickers → r/stocks, r/investing, r/wallstreetbets, r/StockMarket
- PSX tickers   → r/PakistaniInvestors, r/pakistan
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

GLOBAL_SUBREDDITS = ["stocks", "investing", "wallstreetbets", "StockMarket"]
PSX_SUBREDDITS = ["PakistaniInvestors", "pakistan"]
DEFAULT_LIMIT_PER_SUB = 15


def subreddits_for_market(market: str) -> list[str]:
    return PSX_SUBREDDITS if market == "PSX" else GLOBAL_SUBREDDITS


def _build_query(ticker: str, company_name: str | None) -> str:
    if company_name:
        return f'{ticker} OR "{company_name}"'
    return ticker


def _get_reddit_client() -> Any | None:
    """Build a read-only PRAW client, or None when credentials are missing."""
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.info("Reddit credentials not configured; skipping Reddit source")
        return None

    try:
        import praw  # imported lazily so the dependency is optional at runtime
    except Exception as exc:  # noqa: BLE001
        logger.info("praw is not importable; skipping Reddit source: %s", exc)
        return None

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=os.getenv("REDDIT_USER_AGENT", "stocksage-ai/0.1 sentiment"),
        check_for_async=False,
    )
    reddit.read_only = True
    return reddit


def _normalize_submission(submission: Any) -> dict[str, Any] | None:
    title = (getattr(submission, "title", "") or "").strip()
    selftext = (getattr(submission, "selftext", "") or "").strip()
    text = f"{title}\n{selftext}".strip() if selftext else title
    if not text:
        return None

    created = getattr(submission, "created_utc", None)
    created_at = None
    if created:
        try:
            created_at = datetime.fromtimestamp(float(created), tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            created_at = None

    permalink = getattr(submission, "permalink", None)
    return {
        "source": "reddit",
        "id": f"reddit:{getattr(submission, 'id', '')}",
        "text": text,
        "created_at": created_at,
        "author": str(getattr(submission, "author", "") or "") or None,
        "url": f"https://www.reddit.com{permalink}" if permalink else None,
        "label": None,  # Reddit has no built-in bull/bear tag
        "score": int(getattr(submission, "score", 0) or 0),
    }


def _fetch_sync(
    ticker: str,
    market: str,
    company_name: str | None,
    limit_per_sub: int,
) -> list[dict[str, Any]]:
    reddit = _get_reddit_client()
    if reddit is None:
        return []

    query = _build_query(ticker, company_name)
    seen: set[str] = set()
    posts: list[dict[str, Any]] = []

    for sub in subreddits_for_market(market):
        try:
            results = reddit.subreddit(sub).search(
                query, sort="new", time_filter="month", limit=limit_per_sub
            )
            for submission in results:
                normalized = _normalize_submission(submission)
                if normalized is None or normalized["id"] in seen:
                    continue
                seen.add(normalized["id"])
                posts.append(normalized)
        except Exception as exc:  # noqa: BLE001
            # One bad subreddit shouldn't sink the whole source.
            logger.info("Reddit search failed for r/%s (%s): %s", sub, ticker, exc)
            continue

    return posts


async def fetch_reddit_sentiment(
    ticker: str,
    market: str = "GLOBAL",
    company_name: str | None = None,
    limit_per_sub: int = DEFAULT_LIMIT_PER_SUB,
) -> list[dict[str, Any]]:
    """Async wrapper around the blocking PRAW search."""
    return await asyncio.to_thread(
        _fetch_sync, ticker, market, company_name, limit_per_sub
    )
