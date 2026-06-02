"""Filings ingestion pipeline (plan § 4.6).

One-time / periodic indexing: fetch filings → extract text → chunk → embed →
upsert into pgvector. Kept separate from the runtime agent (``agents/filings_agent``)
which only reads from the index.
"""
