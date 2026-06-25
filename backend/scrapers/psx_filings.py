"""PSX annual-report / financial-result fetcher (Phase 4, plan § 4.6).

PSX disclosures live as PDFs/announcements behind the data portal and don't
have a clean text API, so this is **best-effort**: it tries the public financial-
reports listing for a symbol and extracts any readable announcement text. On any
failure — or when nothing parseable is found — it returns ``[]`` so an index run
never breaks on PSX's unstructured side (the same degradation contract the PSX
sentiment scraper uses).

The plan flags PSX filings as "the hard part"; full PDF extraction + LLM cleanup
is a later refinement. The pipeline and RAG runtime are identical to the SEC
path, so PSX coverage improves purely by strengthening this fetcher.
"""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

from scrapers.filings_common import FilingDocument, clean_text

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockSage-AI/0.1; +https://stocksage.ai)"
}

# Public financial-reports listing for a symbol on the PSX data portal.
REPORTS_URL = "https://dps.psx.com.pk/company/{symbol}"

_MAX_WORDS = 40000


async def fetch_latest_filings(
    ticker: str,
    *,
    limit: int = 1,
    max_words: int = _MAX_WORDS,
) -> list[FilingDocument]:
    """Best-effort fetch of recent PSX disclosure text for ``ticker``."""
    ticker = ticker.strip().upper()
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                REPORTS_URL.format(symbol=ticker), headers=_HEADERS, timeout=30
            )
            resp.raise_for_status()
            text = _extract_disclosure_text(resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.info("PSX filings fetch failed for %s: %s", ticker, exc)
        return []

    if not text:
        logger.info("PSX filings: no parseable disclosure text for %s", ticker)
        return []

    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])

    return [
        FilingDocument(
            ticker=ticker,
            market="PSX",
            source="psx",
            external_id=f"psx-company-{ticker}",
            text=text,
            filing_type="disclosure",
            title=f"{ticker} PSX company disclosures",
            url=REPORTS_URL.format(symbol=ticker),
        )
    ][:limit]


def _extract_disclosure_text(html: str) -> str:
    """Pull readable announcement/financial text out of a PSX company page."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    # The portal renders disclosure rows; fall back to whole-page text if the
    # structure isn't recognised. Either way we clean + return plain text.
    candidates = soup.select(
        ".company__disclosures, .announcements, table, .financials, main, article"
    )
    parts = [c.get_text(separator="\n") for c in candidates] if candidates else [
        soup.get_text(separator="\n")
    ]
    text = clean_text("\n".join(parts))
    # Guard against near-empty shells (JS-rendered pages).
    return text if len(text.split()) >= 50 else ""
