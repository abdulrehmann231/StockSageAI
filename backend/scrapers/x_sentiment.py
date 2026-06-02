"""X / Twitter sentiment source (best-effort, credential-free fallback).

X aggressively blocks unauthenticated access, so this source is intentionally
best-effort and *always degrades to an empty list* rather than raising. It tries
three strategies in order:

1. If ``X_BEARER_TOKEN`` is set, query the official recent-search API (reliable,
   free-tier limited).
2. Otherwise try public Nitter mirrors (``NITTER_INSTANCES``, comma-separated)
   until one answers — these come and go, so this is genuinely best-effort.
3. If everything fails, return [] and let the Sentiment Agent carry on with its
   other sources.

Normalized into the Sentiment Agent's post shape. X carries no explicit
bull/bear tag, so ``label`` is always ``None`` and the agent classifies from text.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from scrapers.news_common import clean_text

logger = logging.getLogger(__name__)

OFFICIAL_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
DEFAULT_NITTER_INSTANCES = ("https://nitter.net", "https://nitter.privacydev.net")
DEFAULT_LIMIT = 25
REQUEST_TIMEOUT_SECONDS = 10.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _nitter_instances() -> list[str]:
    configured = os.getenv("NITTER_INSTANCES", "")
    instances = [i.strip().rstrip("/") for i in configured.split(",") if i.strip()]
    return instances or [i.rstrip("/") for i in DEFAULT_NITTER_INSTANCES]


def _build_query(ticker: str, company_name: str | None) -> str:
    """Build a search query favoring the cashtag and exact company name."""
    parts = [f"${ticker}", f'"{ticker}"']
    if company_name and company_name.upper() != ticker.upper():
        parts.append(f'"{company_name}"')
    return " OR ".join(parts)


def _parse_created_at(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    # Official API uses ISO-8601; Nitter renders "May 30, 2026 · 3:00 PM UTC".
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        parsed = None
    if parsed is None:
        nitter = raw.replace("·", "").replace("UTC", "").strip()
        nitter = " ".join(nitter.split())  # collapse the doubled space
        for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
            try:
                parsed = datetime.strptime(nitter, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Official API (preferred when X_BEARER_TOKEN is set)
# --------------------------------------------------------------------------- #


async def _fetch_official(query: str, limit: int) -> list[dict[str, Any]]:
    token = os.getenv("X_BEARER_TOKEN")
    if not token:
        return []

    params = {
        "query": f"({query}) lang:en -is:retweet",
        "max_results": str(max(10, min(100, limit))),
        "tweet.fields": "created_at,public_metrics,author_id",
    }
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "stocksage-ai/0.1"}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(OFFICIAL_SEARCH_URL, params=params, headers=headers)
            if resp.status_code in (401, 403, 429):
                logger.info("X official API unavailable (HTTP %s)", resp.status_code)
                return []
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("X official API call failed: %s", exc)
        return []

    posts: list[dict[str, Any]] = []
    for tweet in (payload.get("data") or [])[:limit]:
        if not isinstance(tweet, dict):
            continue
        text = clean_text(tweet.get("text"))
        if not text:
            continue
        metrics = tweet.get("public_metrics") or {}
        score = int(metrics.get("like_count", 0) or 0) + int(metrics.get("retweet_count", 0) or 0)
        tweet_id = tweet.get("id")
        posts.append(
            {
                "source": "x",
                "id": f"x:{tweet_id}",
                "text": text,
                "created_at": _parse_created_at(tweet.get("created_at")),
                "author": tweet.get("author_id"),
                "url": f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None,
                "label": None,
                "score": score,
            }
        )
    return posts


# --------------------------------------------------------------------------- #
# Nitter fallback (credential-free, best-effort)
# --------------------------------------------------------------------------- #


def parse_nitter_html(html: str, instance: str, limit: int) -> list[dict[str, Any]]:
    """Parse a Nitter search results page into normalized posts.

    Pure function (no I/O) so it can be unit-tested with canned HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    posts: list[dict[str, Any]] = []

    for item in soup.select(".timeline-item"):
        if len(posts) >= limit:
            break
        content = item.select_one(".tweet-content")
        text = clean_text(content.get_text(" ", strip=True)) if content else ""
        if not text:
            continue

        date_link = item.select_one(".tweet-date a")
        url = None
        created_at = None
        if date_link is not None:
            href = date_link.get("href")
            if href:
                url = instance.rstrip("/") + href
            created_at = _parse_created_at(date_link.get("title"))

        username_el = item.select_one(".username")
        author = clean_text(username_el.get_text(strip=True)) if username_el else None

        post_id = url or f"nitter:{abs(hash(text)) % 10_000_000}"
        posts.append(
            {
                "source": "x",
                "id": f"x:{post_id}",
                "text": text,
                "created_at": created_at,
                "author": author,
                "url": url,
                "label": None,
                "score": None,
            }
        )

    return posts


async def _fetch_nitter(query: str, limit: int) -> list[dict[str, Any]]:
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html"}
    for instance in _nitter_instances():
        url = f"{instance}/search"
        params = {"f": "tweets", "q": query}
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True
            ) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:  # noqa: BLE001
            logger.info("Nitter instance %s failed: %s", instance, exc)
            continue
        posts = parse_nitter_html(html, instance, limit)
        if posts:
            return posts
    return []


async def fetch_x_sentiment(
    ticker: str,
    market: str = "GLOBAL",
    company_name: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Best-effort X/Twitter posts for ``ticker``.

    Always returns a list (possibly empty); never raises.
    """
    query = _build_query(ticker.upper(), company_name)

    official = await _fetch_official(query, limit)
    if official:
        return official

    return await _fetch_nitter(query, limit)
