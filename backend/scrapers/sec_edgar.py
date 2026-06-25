"""SEC EDGAR filing fetcher (Phase 4, plan § 4.6).

Uses SEC's **free** JSON APIs — no key, the only requirement is a descriptive
``User-Agent`` (SEC's fair-access policy). Flow:

1. ``company_tickers.json`` maps a ticker → CIK (cached in-process).
2. ``data.sec.gov/submissions/CIK##########.json`` lists recent filings.
3. We pick the latest 10-K / 10-Q and fetch its primary document, strip HTML to
   text, and hand back a ``FilingDocument`` for the indexing pipeline.

Network failures degrade to ``[]`` so a flaky SEC endpoint never crashes an
index run. All network calls are isolated behind small functions so tests stub
them without hitting the wire.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

from scrapers.filings_common import FilingDocument, clean_text

logger = logging.getLogger(__name__)

# SEC requires a UA that identifies the app + a contact. Override via env if you
# fork this; the default is fine for low-volume access.
USER_AGENT = "StockSage AI filings-indexer (contact: filings@stocksage.ai)"
_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

_DEFAULT_FORMS = ("10-K", "10-Q")
_MAX_WORDS = 40000  # cap a single filing's text so embedding stays bounded

# In-process cache of the (large) ticker→CIK map.
_cik_cache: dict[str, int] = {}


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    resp = await client.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


async def _get_text(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


async def _load_cik_map(client: httpx.AsyncClient) -> dict[str, int]:
    if _cik_cache:
        return _cik_cache
    data = await _get_json(client, TICKERS_URL)
    for entry in data.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = entry.get("cik_str")
        if ticker and cik is not None:
            _cik_cache[ticker] = int(cik)
    return _cik_cache


def html_to_text(html: str) -> str:
    """Strip an EDGAR HTML/iXBRL document down to readable plain text."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "table"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return clean_text(text)


def _cap_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


async def fetch_latest_filings(
    ticker: str,
    *,
    forms: tuple[str, ...] = _DEFAULT_FORMS,
    limit: int = 1,
    max_words: int = _MAX_WORDS,
) -> list[FilingDocument]:
    """Fetch up to ``limit`` recent filings of the given ``forms`` for a ticker."""
    ticker = ticker.strip().upper()
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            cik_map = await _load_cik_map(client)
            cik = cik_map.get(ticker)
            if cik is None:
                logger.info("SEC EDGAR: no CIK for %s", ticker)
                return []

            submissions = await _get_json(client, SUBMISSIONS_URL.format(cik=cik))
            picks = _pick_recent(submissions, forms, limit)

            docs: list[FilingDocument] = []
            for pick in picks:
                doc = await _fetch_one(client, ticker, cik, pick, max_words)
                if doc is not None:
                    docs.append(doc)
            return docs
    except Exception as exc:  # noqa: BLE001
        logger.info("SEC EDGAR fetch failed for %s: %s", ticker, exc)
        return []


def _pick_recent(
    submissions: dict[str, Any], forms: tuple[str, ...], limit: int
) -> list[dict[str, Any]]:
    """Walk the parallel-array ``filings.recent`` block, newest-first."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    form_list = recent.get("form") or []
    accession = recent.get("accessionNumber") or []
    primary = recent.get("primaryDocument") or []
    dates = recent.get("filingDate") or []
    wanted = {f.upper() for f in forms}

    picks: list[dict[str, Any]] = []
    for i, form in enumerate(form_list):
        if str(form).upper() not in wanted:
            continue
        picks.append(
            {
                "form": form,
                "accession": accession[i] if i < len(accession) else None,
                "primary_doc": primary[i] if i < len(primary) else None,
                "filing_date": dates[i] if i < len(dates) else None,
            }
        )
        if len(picks) >= limit:
            break
    return picks


async def _fetch_one(
    client: httpx.AsyncClient,
    ticker: str,
    cik: int,
    pick: dict[str, Any],
    max_words: int,
) -> FilingDocument | None:
    accession = pick.get("accession")
    primary_doc = pick.get("primary_doc")
    if not accession or not primary_doc:
        return None
    acc_nodash = accession.replace("-", "")
    url = f"{ARCHIVE_BASE}/{cik}/{acc_nodash}/{primary_doc}"
    html = await _get_text(client, url)
    text = _cap_words(html_to_text(html), max_words)
    if not text:
        return None

    year = None
    if pick.get("filing_date"):
        try:
            year = int(str(pick["filing_date"])[:4])
        except ValueError:
            year = None

    return FilingDocument(
        ticker=ticker,
        market="GLOBAL",
        source="sec_edgar",
        external_id=accession,
        text=text,
        filing_type=pick.get("form"),
        fiscal_year=year,
        title=f"{ticker} {pick.get('form')} ({pick.get('filing_date')})",
        url=url,
    )


def reset_cache() -> None:
    """Test hook — clear the in-process CIK cache."""
    _cik_cache.clear()
