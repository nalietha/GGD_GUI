"""Logging configuration.

Configured once, at the composition root. Every module gets its logger with
``structlog.get_logger(__name__)`` and never touches handlers itself.

V1 used bare ``print()`` in the LED process and the ASP.NET logger in the web
process, so half the system's output went to journald unstructured and the
other half was invisible.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

__all__ = ["configure_logging"]


def configure_logging(*, level: str = "INFO", json: bool = False) -> None:
    """Set up structlog and the stdlib root logger.

    Args:
        level: Threshold name, e.g. ``"DEBUG"``.
        json: Emit JSON lines instead of human-readable console output. Useful
            when shipping logs somewhere; journald reads better without it.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
