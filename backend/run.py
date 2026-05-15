"""Dev server entry point.

Sets WindowsSelectorEventLoopPolicy before uvicorn creates its event loop,
which is required for psycopg-async on Windows. On non-Windows platforms
this is a no-op.

Usage:
    python run.py            # equivalent to: uvicorn main:app --reload
"""

from __future__ import annotations

import asyncio
import sys


def _set_event_loop_policy() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    _set_event_loop_policy()

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        loop="asyncio",
        log_level="info",
    )


if __name__ == "__main__":
    main()
