"""Shared helpers for lightweight news scrapers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

REQUEST_TIMEOUT_SECONDS = 7.0
MAX_SOURCE_ITEMS = 12
AMBIGUOUS_COMPANY_TOKENS = {"luck", "lucky"}
USER_AGENT = (
    "StockSageAI/0.1 (+https://stocksage.local; research bot; "
    "contact=dev@stocksage.local)"
)


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


async def http_get_text(url: str) -> str:
    async with http_client() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value)
    if "<" in raw and ">" in raw:
        text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    else:
        text = raw
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def tag_text(parent: BeautifulSoup, tag_name: str) -> str:
    tag = parent.find(tag_name)
    return tag.get_text(" ", strip=True) if tag else ""


def strip_source_suffix(title: str, source: str) -> str:
    cleaned = clean_text(title)
    suffixes = {source, source.replace(" by ", " "), "Google News"}
    for suffix in suffixes:
        if suffix and cleaned.endswith(f" - {suffix}"):
            return cleaned[: -(len(suffix) + 3)].strip()
        if suffix and cleaned.endswith(f" | {suffix}"):
            return cleaned[: -(len(suffix) + 3)].strip()
        if suffix and cleaned.endswith(f" {suffix}"):
            return cleaned[: -(len(suffix) + 1)].strip()
    return cleaned


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    raw = str(value).strip()
    formats = (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%d %b %Y",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


async def fetch_site_search(
    *,
    source: str,
    search_url: str,
    base_url: str,
    selectors: tuple[str, ...],
) -> list[dict[str, Any]]:
    try:
        html = await http_get_text(search_url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {403, 404}:
            return []
        raise

    soup = BeautifulSoup(html, "html.parser")
    elements = []
    for selector in selectors:
        elements.extend(soup.select(selector))
        if len(elements) >= MAX_SOURCE_ITEMS:
            break

    candidates: list[dict[str, Any]] = []
    for element in elements[:MAX_SOURCE_ITEMS]:
        link_tag = element.find("a", href=True)
        title = clean_text(
            element.get("aria-label")
            or (link_tag.get("title") if link_tag else None)
            or (link_tag.get_text(" ", strip=True) if link_tag else None)
            or element.get_text(" ", strip=True)
        )
        href = link_tag["href"] if link_tag else ""
        if not title or not href:
            continue

        paragraph = element.find("p")
        description = (
            clean_text(paragraph.get_text(" ", strip=True)) if paragraph else ""
        )

        time_tag = element.find("time")
        published_at = None
        if time_tag:
            published_at = parse_datetime(
                time_tag.get("datetime") or time_tag.get_text()
            )

        candidates.append(
            {
                "title": title,
                "url": urljoin(base_url, href),
                "source": source,
                "published_at": published_at,
                "description": description,
            }
        )

    return candidates


def row_matches_query(
    row: dict[str, Any],
    ticker: str,
    company_name: str | None,
) -> bool:
    text = clean_text(
        " ".join(
            str(row.get(key) or "")
            for key in ("title", "description", "content")
        )
    ).lower()
    ticker_lower = ticker.lower()
    if re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", text):
        if ticker_lower in AMBIGUOUS_COMPANY_TOKENS:
            tokens = company_tokens(company_name or "")
            return any(token != ticker_lower and token in text for token in tokens)
        return True
    tokens = company_tokens(company_name or "")
    if any(token in AMBIGUOUS_COMPANY_TOKENS for token in tokens):
        return any(token not in AMBIGUOUS_COMPANY_TOKENS and token in text for token in tokens)
    for token in tokens:
        if token in text:
            return True
    return False


def company_tokens(company_name: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "of",
        "limited",
        "ltd",
        "inc",
        "corp",
        "corporation",
        "company",
        "plc",
        "co",
    }
    return [
        token
        for token in re.findall(r"[a-z0-9]+", company_name.lower())
        if len(token) >= 3 and token not in stopwords
    ]
