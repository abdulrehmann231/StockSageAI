"""NewsAPI scraper for global ticker news."""

from __future__ import annotations

import os
from typing import Any

from scrapers.news_common import MAX_SOURCE_ITEMS, clean_text, http_client, parse_datetime


async def fetch_newsapi_news(
    ticker: str,
    company_name: str | None = None,
) -> list[dict[str, Any]]:
    api_key = os.getenv("NEWSAPI_KEY")
    if not api_key:
        return []

    query = f'"{ticker}"'
    if company_name:
        query = f'("{ticker}" OR "{company_name}")'

    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": str(MAX_SOURCE_ITEMS),
        "apiKey": api_key,
    }
    async with http_client() as client:
        response = await client.get("https://newsapi.org/v2/everything", params=params)
        response.raise_for_status()
        payload = response.json()

    articles: list[dict[str, Any]] = []
    for item in payload.get("articles", [])[:MAX_SOURCE_ITEMS]:
        title = clean_text(item.get("title"))
        url = clean_text(item.get("url"))
        if not title or not url:
            continue
        source = item.get("source") or {}
        articles.append(
            {
                "title": title,
                "url": url,
                "source": clean_text(source.get("name")) or "NewsAPI",
                "published_at": parse_datetime(item.get("publishedAt")),
                "description": clean_text(item.get("description")),
                "content": clean_text(item.get("content")),
            }
        )
    return articles
