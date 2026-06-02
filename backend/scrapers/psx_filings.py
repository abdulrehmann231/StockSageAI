"""PSX annual-report PDF scraper (plan § 4.6, PSX path).

PSX company pages on the data portal (``dps.psx.com.pk/company/<TICKER>``) link
out to financial reports and announcements, many of which are annual-report PDFs.
This module makes a best-effort attempt to discover those PDF links for a ticker,
download them, and extract per-page text with ``pypdf`` so the ingestion pipeline
can chunk and embed them.

PSX report formatting is notoriously messy (scanned tables, multi-column layouts),
so extraction quality varies; LLM cleanup of weak pages is a documented future
enhancement. Like the other scrapers in this project, every entry point degrades
to an empty result on failure — a dead page or a non-PDF link can never raise into
the agent.

A local PDF (already downloaded by hand) can be ingested directly via
``extract_pdf_pages(path)`` without any network access.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO

import httpx

logger = logging.getLogger(__name__)

PSX_COMPANY_URL = "https://dps.psx.com.pk/company/{ticker}"
REQUEST_TIMEOUT_SECONDS = 20.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Heuristics for picking annual-report-ish links out of a company page.
_REPORT_HINTS = ("annual", "financial", "report", "accounts", "10-k", "quarter")
_YEAR_RE = re.compile(r"(19|20)\d{2}")


@dataclass(slots=True)
class FilingPage:
    """One extracted page of a PSX filing PDF, ready for chunking."""

    page: int
    text: str
    source_url: str
    fiscal_year: int | None = None


def _headers() -> dict[str, str]:
    return {"User-Agent": _USER_AGENT, "Accept": "text/html,application/pdf,*/*"}


def _looks_like_report(href: str, link_text: str) -> bool:
    blob = f"{href} {link_text}".lower()
    if not href.lower().endswith(".pdf"):
        return False
    return any(hint in blob for hint in _REPORT_HINTS)


def _guess_year(href: str, link_text: str) -> int | None:
    m = _YEAR_RE.search(f"{link_text} {href}")
    return int(m.group(0)) if m else None


def parse_report_links(html: str, *, base_url: str = "") -> list[tuple[str, int | None]]:
    """Extract ``(absolute_pdf_url, fiscal_year)`` pairs from company-page HTML.

    Pure function (no network) so it is unit-testable. Resolves relative hrefs
    against ``base_url`` and de-duplicates while preserving order.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception:  # noqa: BLE001
        return []

    soup = BeautifulSoup(html or "", "lxml")
    seen: set[str] = set()
    out: list[tuple[str, int | None]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)
        if not _looks_like_report(href, text):
            continue
        url = _absolutize(href, base_url)
        if url in seen:
            continue
        seen.add(url)
        out.append((url, _guess_year(href, text)))
    return out


def _absolutize(href: str, base_url: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    if not base_url:
        return href
    from urllib.parse import urljoin

    return urljoin(base_url, href)


async def discover_report_urls(
    ticker: str, *, client: httpx.AsyncClient | None = None
) -> list[tuple[str, int | None]]:
    """Best-effort discovery of annual-report PDF URLs for a PSX ticker.

    Returns ``[]`` on any failure (page moved, JS-rendered links, network error).
    """
    ticker = ticker.upper()
    url = PSX_COMPANY_URL.format(ticker=ticker)
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS, headers=_headers(), follow_redirects=True
    )
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        links = parse_report_links(resp.text, base_url=url)
        logger.info("PSX %s: discovered %d candidate report PDFs", ticker, len(links))
        return links
    except Exception as exc:  # noqa: BLE001
        logger.warning("PSX report discovery failed for %s: %s", ticker, exc)
        return []
    finally:
        if owns_client:
            await client.aclose()


def extract_pdf_pages(
    source: str | bytes, *, source_url: str | None = None, fiscal_year: int | None = None
) -> list[FilingPage]:
    """Extract per-page text from a PDF given a file path or raw bytes.

    Uses ``pypdf``. Pages whose text extraction yields nothing (e.g. scanned
    images) are skipped. Returns ``[]`` if ``pypdf`` is missing or the PDF is
    unreadable — never raises.
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001
        logger.warning("pypdf unavailable; cannot extract PSX PDF text: %s", exc)
        return []

    try:
        if isinstance(source, bytes):
            reader = PdfReader(BytesIO(source))
            url = source_url or "(in-memory pdf)"
        else:
            reader = PdfReader(source)
            url = source_url or source
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to open PSX PDF (%s): %s", source_url or source, exc)
        return []

    pages: list[FilingPage] = []
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            pages.append(
                FilingPage(
                    page=page_no, text=text, source_url=url, fiscal_year=fiscal_year
                )
            )
    return pages


async def download_pdf(url: str, *, client: httpx.AsyncClient | None = None) -> bytes | None:
    """Download a PDF and return its bytes, or ``None`` on failure / non-PDF."""
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS, headers=_headers(), follow_redirects=True
    )
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        body = resp.content
        if "pdf" not in content_type.lower() and not body[:5].startswith(b"%PDF"):
            logger.info("Skipping non-PDF link %s (content-type=%s)", url, content_type)
            return None
        return body
    except Exception as exc:  # noqa: BLE001
        logger.warning("PSX PDF download failed for %s: %s", url, exc)
        return None
    finally:
        if owns_client:
            await client.aclose()


async def fetch_psx_filing_pages(
    ticker: str, *, max_reports: int = 2
) -> list[FilingPage]:
    """Discover, download, and extract text for a PSX ticker's recent reports.

    End-to-end best-effort: returns a flat list of :class:`FilingPage` across up
    to ``max_reports`` discovered PDFs, newest-looking first. Empty on failure.
    """
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS, headers=_headers(), follow_redirects=True
    ) as client:
        links = await discover_report_urls(ticker, client=client)
        # Prefer links that advertise a year, newest first.
        links.sort(key=lambda lp: lp[1] or 0, reverse=True)

        pages: list[FilingPage] = []
        for url, year in links[:max_reports]:
            body = await download_pdf(url, client=client)
            if body is None:
                continue
            pages.extend(extract_pdf_pages(body, source_url=url, fiscal_year=year))
        logger.info("PSX %s: extracted %d filing pages", ticker.upper(), len(pages))
        return pages
