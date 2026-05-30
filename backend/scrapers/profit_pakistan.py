"""Profit by Pakistan Today news search scraper."""

from __future__ import annotations

from typing import Any
from scrapers.news_common import (
    clean_text,
    company_tokens,
    http_client,
    parse_datetime,
    row_matches_query,
)


async def fetch_profit_pakistan_news(
    ticker: str,
    company_name: str | None = None,
) -> list[dict[str, Any]]:
    queries = _search_queries(ticker, company_name)
    async with http_client() as client:
        articles: list[dict[str, Any]] = []
        for query in queries:
            params = {"search": query, "per_page": "10", "subtype": "post"}
            response = await client.get(
                "https://profit.pakistantoday.com.pk/wp-json/wp/v2/search",
                params=params,
            )
            response.raise_for_status()
            search_rows = response.json()

            for item in search_rows:
                post_url = _post_detail_url(item)
                post_payload: dict[str, Any] | None = None
                if post_url:
                    try:
                        post_response = await client.get(post_url)
                        post_response.raise_for_status()
                        post_payload = post_response.json()
                    except Exception:
                        post_payload = None

                row = _row_from_profit_payload(item, post_payload)
                if row and row_matches_query(row, ticker, company_name):
                    articles.append(row)
        return _dedupe_rows(articles)


def _search_queries(ticker: str, company_name: str | None) -> list[str]:
    queries = [ticker]
    if company_name:
        queries.append(company_name)
        tokens = company_tokens(company_name)
        if tokens:
            queries.append(" ".join(tokens[:2]))
    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique


def _post_detail_url(item: dict[str, Any]) -> str | None:
    links = item.get("_links") or {}
    self_links = links.get("self") or []
    if self_links and isinstance(self_links[0], dict):
        return clean_text(self_links[0].get("href")) or None
    post_id = item.get("id")
    if post_id:
        return f"https://profit.pakistantoday.com.pk/wp-json/wp/v2/posts/{post_id}"
    return None


def _row_from_profit_payload(
    search_item: dict[str, Any],
    post_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = post_payload or search_item
    title_value = payload.get("title")
    if isinstance(title_value, dict):
        title = clean_text(title_value.get("rendered"))
    else:
        title = clean_text(title_value)
    url = clean_text(payload.get("link") or payload.get("url"))
    if not title or not url:
        return None

    excerpt = payload.get("excerpt")
    description = clean_text(excerpt.get("rendered") if isinstance(excerpt, dict) else excerpt)
    content_value = payload.get("content")
    content = clean_text(
        content_value.get("rendered") if isinstance(content_value, dict) else content_value
    )

    return {
        "title": title,
        "url": url,
        "source": "Profit by Pakistan Today",
        "published_at": parse_datetime(payload.get("date_gmt") or payload.get("date")),
        "description": description,
        "content": content,
    }


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = clean_text(row.get("url")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped
