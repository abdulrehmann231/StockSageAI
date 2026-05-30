"""Yahoo Finance RSS news scraper for global tickers."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from scrapers.news_common import (
    MAX_SOURCE_ITEMS,
    clean_text,
    http_get_text,
    parse_datetime,
    tag_text,
)


async def fetch_yahoo_finance_news(ticker: str) -> list[dict[str, Any]]:
    url = (
        "https://feeds.finance.yahoo.com/rss/2.0/headline"
        f"?s={quote_plus(ticker)}&region=US&lang=en-US"
    )
    xml = await http_get_text(url)
    soup = BeautifulSoup(xml, "xml")

    articles: list[dict[str, Any]] = []
    for item in soup.find_all("item")[:MAX_SOURCE_ITEMS]:
        title = clean_text(tag_text(item, "title"))
        link = clean_text(tag_text(item, "link"))
        if not title or not link:
            continue
        articles.append(
            {
                "title": title,
                "url": link,
                "source": "Yahoo Finance",
                "published_at": parse_datetime(tag_text(item, "pubDate")),
                "description": clean_text(tag_text(item, "description")),
            }
        )
    return articles
