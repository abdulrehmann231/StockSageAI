"""Article body extraction for news scraper results."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from scrapers.news_common import clean_text, http_get_text, strip_source_suffix


async def enrich_article(row: dict[str, Any]) -> dict[str, Any]:
    existing = clean_text(row.get("content"))
    if len(existing) >= 300:
        return row
    rss_description = clean_text(row.get("description"))
    fallback_updates = _description_fallback(row)

    url = str(row["url"])
    from scrapers.google_news import resolve_google_news_url

    resolved_url = await resolve_google_news_url(url)
    if resolved_url != url:
        row = {**row, "url": resolved_url}
        url = resolved_url
    elif "news.google." not in urlparse(url).netloc:
        from scrapers.google_news import canonicalize_publisher_url

        canonical_url = await canonicalize_publisher_url(url)
        if canonical_url != url:
            row = {**row, "url": canonical_url}
            url = canonical_url

    try:
        html = await http_get_text(url)
        body = extract_article_body(html)
        page_description = extract_page_description(html)
    except Exception:
        return {**row, **fallback_updates} if fallback_updates else row

    updates: dict[str, str] = {}
    page_description_matches = bool(
        page_description and body_matches_article(page_description, row)
    )
    if page_description_matches and len(page_description) > len(
        clean_text(row.get("description"))
    ):
        updates["description"] = page_description
    if len(body) >= 180 and body_matches_article(body, row):
        updates["content"] = body
    elif len(page_description) >= 120 and page_description_matches:
        updates["content"] = page_description
    elif fallback_updates:
        updates.update(fallback_updates)

    return {**row, **updates} if updates else row


def extract_article_body(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()

    containers = soup.select(
        "article, main, .article, .story, .post, .entry-content, "
        ".post-content, .td-post-content, .article-content, .story__content, "
        ".caas-body, [data-testid='article-body'], .news-detail, .detail-content, "
        "#articleBody, #article-body, .articleBody, .article-body, .article__body, "
        ".entry, .single-post-content, .post-body, .story-body"
    )
    paragraphs: list[str] = []
    for container in containers or [soup]:
        for paragraph in container.find_all(["p", "li"]):
            text = clean_text(paragraph.get_text(" ", strip=True))
            if len(text) >= 40 and not is_boilerplate_sentence(text):
                paragraphs.append(text)
        if len(" ".join(paragraphs)) >= 1200:
            break

    if not paragraphs:
        json_ld_text = _extract_json_ld_article_text(soup)
        if json_ld_text:
            return _truncate(json_ld_text, 4000)

    return _truncate(" ".join(paragraphs), 4000)


def _extract_json_ld_article_text(soup: BeautifulSoup) -> str:
    import json

    parts: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph")
            if isinstance(graph, list):
                candidates.extend(item for item in graph if isinstance(item, dict))
            article_body = clean_text(candidate.get("articleBody"))
            description = clean_text(candidate.get("description"))
            headline = clean_text(candidate.get("headline"))
            for text in (headline, description, article_body):
                if len(text) >= 40 and not is_boilerplate_sentence(text):
                    parts.append(text)
    return " ".join(dict.fromkeys(parts))


def extract_page_description(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    selectors = (
        ("meta", {"name": "description"}),
        ("meta", {"property": "og:description"}),
        ("meta", {"name": "twitter:description"}),
    )
    descriptions: list[str] = []
    for name, attrs in selectors:
        tag = soup.find(name, attrs=attrs)
        if tag and tag.get("content"):
            text = clean_text(tag.get("content"))
            if len(text) >= 40 and not is_boilerplate_sentence(text):
                descriptions.append(text)
    return max(descriptions, key=len, default="")


def _description_fallback(row: dict[str, Any]) -> dict[str, str]:
    description = clean_text(row.get("description"))
    title = strip_source_suffix(str(row.get("title") or ""), str(row.get("source") or ""))
    if len(description) < 80:
        return {}
    if _summary_is_headline_like(description, title):
        return {}
    if is_boilerplate_sentence(description):
        return {}
    return {"content": description}


def body_matches_article(body: str, row: dict[str, Any]) -> bool:
    body_key = _clean_for_key(body)
    title_tokens = [
        token
        for token in _clean_for_key(
            strip_source_suffix(str(row.get("title") or ""), str(row.get("source") or ""))
        ).split()
        if len(token) >= 4
        and token
        not in {
            "after",
            "amid",
            "with",
            "from",
            "this",
            "that",
            "pakistan",
            "market",
            "news",
        }
    ]
    if not title_tokens:
        return True
    matches = sum(1 for token in title_tokens[:8] if token in body_key)
    required_matches = 2 if len(title_tokens) >= 3 else 1
    if matches < required_matches:
        return False
    if len(title_tokens) >= 4:
        return any(token in body_key for token in title_tokens[2:8])
    return True


def has_scraped_article_text(row: dict[str, Any]) -> bool:
    content = clean_text(row.get("content"))
    if len(content) >= 180:
        return True
    description = clean_text(row.get("description"))
    title = strip_source_suffix(str(row.get("title") or ""), str(row.get("source") or ""))
    if len(description) >= 140 and not _summary_is_headline_like(description, title):
        return True
    title_lower = title.lower()
    if "solar" in title_lower and "news.google." not in urlparse(str(row.get("url"))).netloc:
        return True
    return _is_trusted_title_only_result(row)


def _is_trusted_title_only_result(row: dict[str, Any]) -> bool:
    if "news.google." not in urlparse(str(row.get("url"))).netloc:
        return False
    source = clean_text(row.get("source")).lower()
    trusted_sources = {
        "business recorder",
        "profit by pakistan today",
        "profit pakistan",
        "propakistani",
        "the news pakistan",
        "mettis global",
        "dawn",
        "dawn business",
        "cemnet.com",
    }
    if source not in trusted_sources:
        return False
    title = strip_source_suffix(str(row.get("title") or ""), str(row.get("source") or "")).lower()
    material_terms = (
        "stake",
        "acquisition",
        "appoints",
        "ceo",
        "profit",
        "earnings",
        "revenue",
        "sales growth",
        "sales",
        "dispatches",
        "lng",
        "cargo",
        "terminal",
        "energy security",
        "energy",
        "solar",
        "capacity",
        "plant",
        "production",
        "project",
        "partnership",
        "agreement",
        "expansion",
        "investment",
        "financing",
        "loan",
        "approval",
        "board",
        "subsidiary",
        "group",
        "cement",
        "motor",
        "dividend",
        "results",
    )
    return any(term in title for term in material_terms)


def is_boilerplate_sentence(sentence: str) -> bool:
    text = sentence.lower()
    boilerplate_terms = (
        "all information and data",
        "disclosure policy",
        "terms of use",
        "privacy policy",
        "click here",
        "read more",
        "this article first appeared",
        "for more information",
        "mg news |",
        "morning breeze aims",
        "aims to lessen the load for its readers",
        "follow us on",
        "subscribe to",
        "advertisement",
    )
    return any(term in text for term in boilerplate_terms)


def _summary_is_headline_like(summary: str, title: str) -> bool:
    summary_key = _clean_for_key(summary)
    title_key = _clean_for_key(title)
    if not summary_key:
        return True
    if summary_key == title_key:
        return True
    if summary_key in title_key or title_key in summary_key:
        return True
    return len(_split_sentences(summary)) == 0 and len(summary.split()) <= 14


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _clean_for_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
