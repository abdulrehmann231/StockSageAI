"""Seed the stocks table from scripts/seed_stocks.json.

Run from repo root:
    python -m scripts.populate_stocks

Re-runnable: uses upsert (ON CONFLICT DO UPDATE) keyed on ticker.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from db.models import Stock  # noqa: E402
from db.session import Base, SessionLocal, engine  # noqa: E402


async def populate() -> int:
    seed_path = REPO_ROOT / "scripts" / "seed_stocks.json"
    with seed_path.open(encoding="utf-8") as fh:
        rows: list[dict] = json.load(fh)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    stmt = insert(Stock).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Stock.ticker],
        set_={
            "name": stmt.excluded.name,
            "market": stmt.excluded.market,
            "sector": stmt.excluded.sector,
            "industry": stmt.excluded.industry,
            "currency": stmt.excluded.currency,
            "is_active": True,
        },
    )

    async with SessionLocal() as session:
        await session.execute(stmt)
        await session.commit()

    await engine.dispose()
    return len(rows)


if __name__ == "__main__":
    count = asyncio.run(populate())
    print(f"Seeded {count} stocks.")
