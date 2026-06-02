"""Telegram public-channel sentiment source (credential-free).

Public Telegram channels expose a read-only web preview at
``https://t.me/s/<channel>`` that renders recent posts as plain HTML — no
Telethon, ``API_ID``/``API_HASH``, or phone-login session required. We scrape
that preview for finance/PSX channels, keep messages that mention the target
ticker / company, and normalize them into the Sentiment Agent's post shape.

This is primarily aimed at PSX coverage (plan § 4.7), where Reddit/StockTwits
are thin. Channels are configurable via the ``PSX_TELEGRAM_CHANNELS`` env var
(comma-separated handles). Everything degrades to an empty list on failure so a
dead channel or a network hiccup can never sink the Sentiment Agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from scrapers.news_common import clean_text, company_tokens

logger = logging.getLogger(__name__)

TELEGRAM_PREVIEW_URL = "https://t.me/s/{channel}"
# Sensible PSX-finance defaults; override with PSX_TELEGRAM_CHANNELS. Unknown or
# private channels simply 404 and are skipped.
DEFAULT_PSX_CHANNELS = ("psxstocks", "pakistanstockexchange")
DEFAULT_LIMIT_PER_CHANNEL = 20
REQUEST_TIMEOUT_SECONDS = 10.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def channels_for_market(market: str) -> list[str]:
    """Resolve the channel handles to scrape for a market."""
    configured = os.getenv("PSX_TELEGRAM_CHANNELS", "")
    handles = [h.strip().lstrip("@") for h in configured.split(",") if h.strip()]
    if handles:
        return handles
    # Telegram coverage in this project is PSX-oriented; global tickers rely on
    # Reddit/StockTwits/X unless channels are explicitly configured.
    return list(DEFAULT_PSX_CHANNELS) if market.upper() == "PSX" else []


def _mentions(text: str, ticker: str, company_name: str | None) -> bool:
    low = text.lower()
    ticker_lower = ticker.lower()
    if re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", low):
        return True
    for token in company_tokens(company_name or ""):
        if len(token) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", low):
            return True
    return False


def _parse_views(raw: str | None) -> int | None:
    """Turn Telegram's compact view counts ('1.2K', '3.4M') into an int."""
    if not raw:
        return None
    text = raw.strip().upper().replace(",", "")
    match = re.match(r"^([0-9]*\.?[0-9]+)\s*([KM]?)$", text)
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2)
    if suffix == "K":
        value *= 1_000
    elif suffix == "M":
        value *= 1_000_000
    return int(value)


def _parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_channel_html(
    html: str,
    channel: str,
    ticker: str,
    company_name: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Parse a ``t.me/s/<channel>`` preview page into normalized posts.

    Pure function (no I/O) so it can be unit-tested with canned HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    posts: list[dict[str, Any]] = []

    for message in soup.select("div.tgme_widget_message"):
        if len(posts) >= limit:
            break
        text_el = message.select_one(".tgme_widget_message_text")
        text = clean_text(text_el.get_text(" ", strip=True)) if text_el else ""
        if not text or not _mentions(text, ticker, company_name):
            continue

        date_link = message.select_one("a.tgme_widget_message_date")
        time_el = date_link.select_one("time") if date_link else None
        created_at = _parse_created_at(time_el.get("datetime")) if time_el else None
        url = date_link.get("href") if date_link else None

        data_post = message.get("data-post") or ""
        post_id = f"telegram:{data_post}" if data_post else f"telegram:{abs(hash(text)) % 10_000_000}"

        views_el = message.select_one(".tgme_widget_message_views")
        score = _parse_views(views_el.get_text(strip=True)) if views_el else None

        posts.append(
            {
                "source": "telegram",
                "id": post_id,
                "text": text,
                "created_at": created_at,
                "author": channel,
                "url": url,
                "label": None,  # Telegram has no built-in bull/bear tag
                "score": score,
            }
        )

    return posts


async def _fetch_channel_html(channel: str) -> str | None:
    """Fetch a channel's preview HTML, or None on any failure.

    Isolated as the single network seam so tests can stub it.
    """
    url = TELEGRAM_PREVIEW_URL.format(channel=channel)
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html"}
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                logger.info("Telegram channel not found: %s", channel)
                return None
            resp.raise_for_status()
            return resp.text
    except Exception as exc:  # noqa: BLE001 — best-effort source
        logger.info("Telegram fetch failed for %s: %s", channel, exc)
        return None


async def _fetch_one_channel(
    channel: str,
    ticker: str,
    company_name: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    html = await _fetch_channel_html(channel)
    if not html:
        return []
    try:
        return parse_channel_html(html, channel, ticker, company_name, limit)
    except Exception as exc:  # noqa: BLE001
        logger.info("Telegram parse failed for %s: %s", channel, exc)
        return []


async def fetch_telegram_sentiment(
    ticker: str,
    market: str = "PSX",
    company_name: str | None = None,
    limit_per_channel: int = DEFAULT_LIMIT_PER_CHANNEL,
) -> list[dict[str, Any]]:
    """Scrape configured Telegram channels for ticker mentions.

    Always returns a list (possibly empty); never raises, since this is a
    best-effort source.
    """
    channels = channels_for_market(market)
    if not channels:
        return []

    results = await asyncio.gather(
        *(
            _fetch_one_channel(channel, ticker, company_name, limit_per_channel)
            for channel in channels
        ),
        return_exceptions=True,
    )

    posts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        if isinstance(result, Exception):
            logger.info("Telegram channel task failed for %s: %s", ticker, result)
            continue
        for post in result:
            if post["id"] in seen:
                continue
            seen.add(post["id"])
            posts.append(post)
    return posts
