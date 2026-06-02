"""Unit tests for the PSX annual-report PDF scraper's pure helpers.

No network and no real PDFs — link parsing and guards only. The download +
pypdf extraction path is exercised by the live e2e script.
"""

from __future__ import annotations

from scrapers.psx_filings import (
    _guess_year,
    _looks_like_report,
    extract_pdf_pages,
    parse_report_links,
)


def test_looks_like_report_requires_pdf_and_hint():
    assert _looks_like_report("/files/ENGRO-annual-2023.pdf", "Annual Report 2023")
    assert _looks_like_report("/x/financial-statements.pdf", "Financials")
    # PDF but no report-ish hint → rejected.
    assert not _looks_like_report("/x/notice.pdf", "AGM Notice venue")
    # Report-ish text but not a PDF → rejected.
    assert not _looks_like_report("/x/annual-report", "Annual Report")


def test_guess_year():
    assert _guess_year("/r/2023.pdf", "Annual Report 2023") == 2023
    assert _guess_year("/r/report.pdf", "Financials") is None


def test_parse_report_links_absolutizes_and_dedupes():
    html = """
    <html><body>
      <a href="/downloads/annual-report-2023.pdf">Annual Report 2023</a>
      <a href="https://cdn.example.com/financials-2022.pdf">Financial Statements 2022</a>
      <a href="/downloads/annual-report-2023.pdf">Annual Report 2023 (dup)</a>
      <a href="/agm-notice.pdf">AGM Notice</a>
      <a href="/home">Home</a>
    </body></html>
    """
    links = parse_report_links(html, base_url="https://dps.psx.com.pk/company/ENGRO")
    urls = [u for u, _ in links]
    assert "https://dps.psx.com.pk/downloads/annual-report-2023.pdf" in urls
    assert "https://cdn.example.com/financials-2022.pdf" in urls
    # dedup + non-report filtered out
    assert len(urls) == 2
    years = dict(links)
    assert years["https://cdn.example.com/financials-2022.pdf"] == 2022


def test_parse_report_links_empty_html():
    assert parse_report_links("", base_url="https://x") == []


def test_extract_pdf_pages_bad_input_returns_empty():
    # Not a valid PDF → pypdf raises internally → we return [] (never raise).
    assert extract_pdf_pages(b"not a pdf", source_url="x") == []
