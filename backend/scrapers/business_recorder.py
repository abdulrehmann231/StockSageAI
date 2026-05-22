"""Business Recorder news search scraper."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from scrapers.news_common import fetch_site_search, row_matches_query


async def fetch_business_recorder_news(
    ticker: str,
    company_name: str | None = None,
) -> list[dict[str, Any]]:
    query = company_name or ticker
    rows = await fetch_site_search(
        source="Business Recorder",
        search_url=f"https://www.brecorder.com/search?q={quote_plus(query)}",
        base_url="https://www.brecorder.com",
        selectors=(".story", "article", ".story__title", "h2"),
    )
    return [row for row in rows if row_matches_query(row, ticker, company_name)]
