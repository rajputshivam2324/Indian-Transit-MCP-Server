"""Thin MCP adapter: composition root, tools, and server entrypoint."""

from __future__ import annotations

from .app import Container, build_container
from .main import create_app, main

__all__ = ["Container", "build_container", "create_app", "main"]
