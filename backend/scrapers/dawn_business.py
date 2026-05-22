"""Dawn Business news search scraper."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from urllib.parse import urlparse

from scrapers.news_common import fetch_site_search, row_matches_query


async def fetch_dawn_business_news(
    ticker: str,
    company_name: str | None = None,
) -> list[dict[str, Any]]:
    query = company_name or ticker
    rows = await fetch_site_search(
        source="Dawn Business",
        search_url=(
            "https://www.dawn.com/search?"
            f"q={quote_plus(query)}"
        ),
        base_url="https://www.dawn.com",
        selectors=("article", ".box", ".story", ".search-result"),
    )
    return [
        row
        for row in rows
        if urlparse(str(row.get("url") or "")).netloc.endswith("dawn.com")
        and row_matches_query(row, ticker, company_name)
    ]
