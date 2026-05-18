"""FastAPI middleware for request processing.

Includes request ID injection, timing, and logging.
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from core.logging import generate_request_id, get_logger, request_id_ctx

logger = get_logger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject request ID into all requests for tracing.

    The request ID is:
    1. Set in a context variable for access in async code
    2. Added to the response headers as X-Request-ID
    3. Logged with request start/end events
    """

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        # Use incoming request ID or generate new one
        request_id = request.headers.get("X-Request-ID") or generate_request_id()

        # Set context variable for logging
        token = request_id_ctx.set(request_id)

        start_time = time.perf_counter()

        try:
            # Log request start
            logger.debug(
                "Request started",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "client_ip": request.client.host if request.client else None,
                },
            )

            response = await call_next(request)

            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Log request completion
            log_level = "warning" if response.status_code >= 400 else "info"
            getattr(logger, log_level)(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            return response

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(
                "Request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                    "error": str(exc),
                },
            )
            raise

        finally:
            # Reset context variable
            request_id_ctx.reset(token)
