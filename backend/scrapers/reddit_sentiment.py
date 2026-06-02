"""Reddit sentiment source — credential-free scraping, with PRAW upgrade.

Searches the relevant investing subreddits for a ticker / company name over the
last month and normalizes submissions into the Sentiment Agent's post shape.

Two retrieval paths, picked automatically:
- **PRAW** (read-only) when ``REDDIT_CLIENT_ID`` / ``REDDIT_CLIENT_SECRET`` are
  set — the most robust option, run inside ``asyncio.to_thread`` since PRAW is
  synchronous.
- **Public JSON** (``reddit.com/r/<sub>/search.json``) when no credentials are
  configured — Reddit's read-only search works without OAuth given a real
  User-Agent, so the source still produces posts out of the box. Subject to
  Reddit rate limiting, so it degrades to empty on 403/429.

Either way the fetcher never raises for missing config; it just returns what it
can so the agent can blend it with its other sources.

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

import httpx

logger = logging.getLogger(__name__)

GLOBAL_SUBREDDITS = ["stocks", "investing", "wallstreetbets", "StockMarket"]
PSX_SUBREDDITS = ["PakistaniInvestors", "pakistan"]
DEFAULT_LIMIT_PER_SUB = 15
_PUBLIC_JSON_TIMEOUT = 12.0
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def subreddits_for_market(market: str) -> list[str]:
    return PSX_SUBREDDITS if market == "PSX" else GLOBAL_SUBREDDITS


def _build_query(ticker: str, company_name: str | None) -> str:
    if company_name:
        return f'{ticker} OR "{company_name}"'
    return ticker


def _reddit_configured() -> bool:
    return bool(os.getenv("REDDIT_CLIENT_ID") and os.getenv("REDDIT_CLIENT_SECRET"))


def _get_reddit_client() -> Any | None:
    """Build a read-only PRAW client, or None when credentials are missing."""
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.info("Reddit credentials not configured; using public JSON fallback")
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


def _normalize_json(data: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a Reddit public-JSON listing child's ``data`` object."""
    title = (data.get("title") or "").strip()
    selftext = (data.get("selftext") or "").strip()
    text = f"{title}\n{selftext}".strip() if selftext else title
    if not text:
        return None

    created = data.get("created_utc")
    created_at = None
    if created:
        try:
            created_at = datetime.fromtimestamp(float(created), tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            created_at = None

    permalink = data.get("permalink")
    return {
        "source": "reddit",
        "id": f"reddit:{data.get('id', '')}",
        "text": text,
        "created_at": created_at,
        "author": (str(data.get("author")) or None) if data.get("author") else None,
        "url": f"https://www.reddit.com{permalink}" if permalink else None,
        "label": None,  # Reddit has no built-in bull/bear tag
        "score": int(data.get("score", 0) or 0),
    }


async def _fetch_public_json(
    ticker: str,
    market: str,
    company_name: str | None,
    limit_per_sub: int,
) -> list[dict[str, Any]]:
    """Credential-free retrieval via Reddit's public search JSON endpoint."""
    query = _build_query(ticker, company_name)
    headers = {"User-Agent": _BROWSER_USER_AGENT, "Accept": "application/json"}
    seen: set[str] = set()
    posts: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        timeout=_PUBLIC_JSON_TIMEOUT, follow_redirects=True
    ) as client:
        for sub in subreddits_for_market(market):
            params = {
                "q": query,
                "restrict_sr": "1",
                "sort": "new",
                "t": "month",
                "limit": str(limit_per_sub),
            }
            try:
                resp = await client.get(
                    f"https://www.reddit.com/r/{sub}/search.json",
                    params=params,
                    headers=headers,
                )
                if resp.status_code in (403, 429):
                    logger.info("Reddit public JSON throttled for r/%s (HTTP %s)", sub, resp.status_code)
                    continue
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001 — one bad sub shouldn't sink the source
                logger.info("Reddit public JSON failed for r/%s (%s): %s", sub, ticker, exc)
                continue

            children = (payload.get("data") or {}).get("children") or []
            for child in children:
                data = child.get("data") if isinstance(child, dict) else None
                if not isinstance(data, dict):
                    continue
                normalized = _normalize_json(data)
                if normalized is None or normalized["id"] in seen:
                    continue
                seen.add(normalized["id"])
                posts.append(normalized)

    return posts


async def fetch_reddit_sentiment(
    ticker: str,
    market: str = "GLOBAL",
    company_name: str | None = None,
    limit_per_sub: int = DEFAULT_LIMIT_PER_SUB,
) -> list[dict[str, Any]]:
    """Fetch Reddit posts, preferring PRAW and falling back to public JSON.

    Uses the authenticated PRAW client when credentials are configured (most
    robust); otherwise scrapes Reddit's public search JSON so the source works
    with no setup. Always returns a list — never raises for missing config.
    """
    if _reddit_configured():
        return await asyncio.to_thread(
            _fetch_sync, ticker, market, company_name, limit_per_sub
        )
    return await _fetch_public_json(ticker, market, company_name, limit_per_sub)
