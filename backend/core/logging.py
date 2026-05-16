"""Structured logging configuration with request ID tracking.

Provides JSON-formatted logs in production and human-readable logs
in development. Request IDs are propagated through context variables
for correlation across async operations.

Usage:
    from core.logging import get_logger, request_id_ctx

    logger = get_logger(__name__)
    logger.info("Processing request", extra={"user_id": user.id})
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from core.config import get_settings

# Context variable for request ID tracking across async boundaries
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return str(uuid.uuid4())[:8]  # Short ID for readability


class RequestIdFilter(logging.Filter):
    """Inject request_id into all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get() or "-"
        return True


class JSONFormatter(logging.Formatter):
    """JSON log formatter for production environments.

    Outputs structured logs that can be parsed by log aggregators
    like CloudWatch, Datadog, or ELK stack.
    """

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }

        # Add location info for errors
        if record.levelno >= logging.WARNING:
            log_data["location"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add any extra fields passed via extra={}
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "exc_info",
                "exc_text",
                "thread",
                "threadName",
                "request_id",
                "message",
                "taskName",
            ):
                log_data[key] = value

        return json.dumps(log_data, default=str)


class DevFormatter(logging.Formatter):
    """Human-readable formatter for development.

    Includes colors and request ID prefix for easy scanning.
    """

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", "-")
        color = self.COLORS.get(record.levelname, "")

        # Format: [request_id] LEVEL logger: message
        prefix = f"[{request_id}] {color}{record.levelname:8}{self.RESET}"
        message = f"{prefix} {record.name}: {record.getMessage()}"

        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)

        return message


def setup_logging() -> None:
    """Configure logging based on environment.

    Call this once at application startup before any logging occurs.
    """
    settings = get_settings()

    # Determine log level
    log_level = logging.DEBUG if settings.debug else logging.INFO

    # Choose formatter based on environment
    if settings.debug:
        formatter = DevFormatter()
    else:
        formatter = JSONFormatter()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add console handler with our formatter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(RequestIdFilter())
    root_logger.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with request ID filtering.

    Args:
        name: Usually __name__ of the calling module.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    return logger
