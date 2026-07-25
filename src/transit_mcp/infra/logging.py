"""Logging helpers.

MCP servers communicate over stdio, so **all** logs must go to stderr to avoid
corrupting the protocol stream on stdout.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Attach a single stderr handler to the package logger (idempotent)."""
    global _CONFIGURED
    logger = logging.getLogger("transit_mcp")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``transit_mcp`` namespace."""
    return logging.getLogger(f"transit_mcp.{name}")
