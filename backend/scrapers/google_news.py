"""Google News RSS scraper and wrapper-URL resolver."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from html import unescape
from typing import Any
from urllib.parse import quote, quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from scrapers.news_common import (
    MAX_SOURCE_ITEMS,
    clean_text,
    http_client,
    http_get_text,
    parse_datetime,
    strip_source_suffix,
    tag_text,
)

logger = logging.getLogger(__name__)

MAX_GOOGLE_QUERIES = 5
GOOGLE_DECODE_COOLDOWN_SECONDS = 300
_DECODE_CACHE: dict[str, str] = {}
_DECODE_DISABLED_UNTIL = 0.0


async def fetch_google_news(
    ticker: str,
    market: str,
    company_name: str | None = None,
) -> list[dict[str, Any]]:
    queries = _google_news_queries(ticker, market, company_name)
    fetched = await asyncio.gather(
        *(_fetch_google_news_query(query) for query in queries),
        return_exceptions=True,
    )

    articles: list[dict[str, Any]] = []
    for result in fetched:
        if isinstance(result, Exception):
            logger.debug("Google News query failed: %s", result)
            continue
        articles.extend(result)
    return _dedupe_rows(articles)


def _google_news_queries(
    ticker: str,
    market: str,
    company_name: str | None,
) -> list[str]:
    query_terms = [f'"{ticker}"']
    cleaned_company = clean_text(company_name)
    if cleaned_company and cleaned_company.upper() != ticker.upper():
        query_terms.append(f'"{cleaned_company}"')

    if market == "PSX":
        primary_query = " OR ".join(query_terms)
        queries = [
            f"({primary_query}) "
            '("PSX" OR "Pakistan Stock Exchange" OR "Pakistan" OR "Karachi")'
        ]
        if cleaned_company and cleaned_company.upper() != ticker.upper():
            company_core = _company_search_core(cleaned_company)
            queries.extend(
                [
                    f'"{cleaned_company}" Pakistan',
                    f'"{company_core}" Pakistan stock',
                    f'"{company_core}" PSX',
                    f'"{company_core}" site:brecorder.com OR site:profit.pakistantoday.com.pk OR site:mettisglobal.news OR site:dawn.com',
                ]
            )
        return _unique_strings(queries)[:MAX_GOOGLE_QUERIES]

    query_terms.append("stock")
    return [" OR ".join(query_terms)]


def _company_search_core(company_name: str) -> str:
    tokens = _company_tokens(company_name)
    if not tokens:
        return company_name
    return " ".join(tokens[:3])


def _company_tokens(company_name: str) -> list[str]:
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


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


async def _fetch_google_news_query(query: str) -> list[dict[str, Any]]:
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    xml = await http_get_text(url)
    soup = BeautifulSoup(xml, "xml")

    articles: list[dict[str, Any]] = []
    for item in soup.find_all("item")[:MAX_SOURCE_ITEMS]:
        title = clean_text(tag_text(item, "title"))
        link = clean_text(tag_text(item, "link"))
        description = clean_text(tag_text(item, "description"))
        if not title or not link:
            continue

        source = clean_text(tag_text(item, "source")) or "Google News"
        articles.append(
            {
                "title": title,
                "url": link,
                "source": source,
                "published_at": parse_datetime(tag_text(item, "pubDate")),
                "description": strip_source_suffix(description, source),
            }
        )
    return articles


async def resolve_google_news_url(url: str) -> str:
    """Resolve a Google News RSS wrapper URL to the publisher URL when possible."""
    global _DECODE_DISABLED_UNTIL

    cached = _DECODE_CACHE.get(url)
    if cached:
        return cached

    parsed = urlparse(url)
    if "news.google." not in parsed.netloc:
        return url

    if time.monotonic() < _DECODE_DISABLED_UNTIL:
        return url

    redirected_url = await _follow_google_news_redirect(url)
    if redirected_url:
        _DECODE_CACHE[url] = redirected_url
        return redirected_url

    direct_url = _decode_legacy_google_news_url(url)
    if direct_url:
        _DECODE_CACHE[url] = direct_url
        return direct_url

    article_id = _google_news_article_id(url)
    if not article_id:
        return url

    params = await _google_news_decoding_params(article_id)
    if not params:
        return url

    timestamp, signature = params
    decoded = await _decode_google_news_batch(article_id, timestamp, signature)
    if decoded:
        canonical = await canonicalize_publisher_url(decoded)
        _DECODE_CACHE[url] = canonical
        return canonical
    fallback_url = await _extract_publisher_url_from_google_page(url)
    if fallback_url:
        _DECODE_CACHE[url] = fallback_url
        return fallback_url
    return url


async def _follow_google_news_redirect(url: str) -> str | None:
    try:
        async with http_client() as client:
            response = await client.get(url)
            response.raise_for_status()
            final_url = str(response.url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Google News redirect follow failed for %s: %s", url, exc)
        return None

    if final_url and "news.google." not in urlparse(final_url).netloc:
        return await canonicalize_publisher_url(final_url)
    return None


async def canonicalize_publisher_url(url: str) -> str:
    """Follow publisher redirects and return the final URL when it is safe to do so."""
    if not url or "news.google." in urlparse(url).netloc:
        return url
    try:
        async with http_client() as client:
            response = await client.head(url)
            if response.status_code in {403, 405}:
                response = await client.get(url)
            response.raise_for_status()
            final_url = str(response.url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Canonical URL resolution failed for %s: %s", url, exc)
        return url

    if final_url and "news.google." not in urlparse(final_url).netloc:
        return final_url
    return url


async def _extract_publisher_url_from_google_page(url: str) -> str | None:
    """Best-effort publisher extraction when Google's batch decoder fails."""
    try:
        html = await http_get_text(url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Google News wrapper fetch failed for %s: %s", url, exc)
        return None

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    selectors = (
        ("link", "rel", "canonical", "href"),
        ("meta", "property", "og:url", "content"),
        ("meta", "name", "twitter:url", "content"),
    )
    for tag_name, attr_name, attr_value, target_attr in selectors:
        tag = soup.find(tag_name, attrs={attr_name: attr_value})
        if tag and tag.get(target_attr):
            candidates.append(clean_text(tag.get(target_attr)))

    for link in soup.find_all("a", href=True):
        href = clean_text(link.get("href"))
        if href.startswith("./"):
            href = f"https://news.google.com{href[1:]}"
        candidates.append(href)

    for candidate in candidates:
        publisher_url = _publisher_candidate_url(candidate)
        if publisher_url:
            return await canonicalize_publisher_url(publisher_url)
    return None


def _publisher_candidate_url(url: str) -> str | None:
    if not url:
        return None
    if url.startswith("http"):
        parsed = urlparse(url)
        if "news.google." not in parsed.netloc:
            return url
        nested = re.search(r"[?&](?:url|u)=([^&]+)", url)
        if nested:
            decoded = unquote(nested.group(1))
            if decoded.startswith("http") and "news.google." not in urlparse(decoded).netloc:
                return decoded
    return None


def _decode_legacy_google_news_url(url: str) -> str | None:
    article_id = _google_news_article_id(url)
    if not article_id:
        return None

    padded = article_id + "=" * (-len(article_id) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("latin1", errors="ignore")
    except Exception:  # noqa: BLE001
        return None

    match = re.search(r"https?://[^\x00-\x1f\"'<>\\]+", decoded)
    if not match:
        return None
    return unquote(match.group(0))


def _google_news_article_id(url: str) -> str | None:
    parsed = urlparse(url)
    match = re.search(r"/(?:rss/)?(?:articles|read)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    return None


async def _google_news_decoding_params(article_id: str) -> tuple[str, str] | None:
    candidate_urls = (
        f"https://news.google.com/articles/{article_id}",
        f"https://news.google.com/rss/articles/{article_id}",
        f"https://news.google.com/read/{article_id}",
    )
    for candidate_url in candidate_urls:
        try:
            html = await http_get_text(f"{candidate_url}?hl=en-US&gl=US&ceid=US:en")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Google News params fetch failed for %s: %s", candidate_url, exc)
            continue

        soup = BeautifulSoup(html, "html.parser")
        element = soup.select_one("[data-n-a-sg][data-n-a-ts]")
        if element:
            timestamp = clean_text(element.get("data-n-a-ts"))
            signature = clean_text(element.get("data-n-a-sg"))
            if timestamp and signature:
                return timestamp, signature

        match = re.search(
            r'data-n-a-sg="(?P<signature>[^"]+)".{0,200}data-n-a-ts="(?P<timestamp>\d+)"',
            html,
            re.DOTALL,
        )
        if not match:
            match = re.search(
                r'data-n-a-ts="(?P<timestamp>\d+)".{0,200}data-n-a-sg="(?P<signature>[^"]+)"',
                html,
                re.DOTALL,
            )
        if match:
            return match.group("timestamp"), unescape(match.group("signature"))

    return None


async def _decode_google_news_batch(
    article_id: str,
    timestamp: str,
    signature: str,
) -> str | None:
    global _DECODE_DISABLED_UNTIL

    payload = [
        "Fbv4je",
        (
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",'
            'null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],'
            f'1,1,null,0,0,null,0],"{article_id}",{timestamp},"{signature}"]'
        ),
    ]

    try:
        async with http_client() as client:
            response = await client.post(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                data=f"f.req={quote(json.dumps([[payload]], separators=(',', ':')))}",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "Referer": "https://news.google.com/",
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            _DECODE_DISABLED_UNTIL = time.monotonic() + GOOGLE_DECODE_COOLDOWN_SECONDS
        logger.debug("Google News decode failed for %s: %s", article_id, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Google News decode failed for %s: %s", article_id, exc)
        return None

    try:
        parsed_data = json.loads(response.text.split("\n\n", 1)[1])[:-2]
        decoded_payload = json.loads(parsed_data[0][2])
        decoded_url = clean_text(decoded_payload[1])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Google News decode parse failed for %s: %s", article_id, exc)
        return None

    if decoded_url and "news.google." not in urlparse(decoded_url).netloc:
        return decoded_url
    return None


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = _normalize_url(str(row.get("url") or "")) or _clean_for_key(
            str(row.get("title") or "")
        )
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _normalize_url(url: str) -> str:
    return re.sub(r"[?#].*$", "", url).rstrip("/").lower()


def _clean_for_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
