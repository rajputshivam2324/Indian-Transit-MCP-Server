"""Tool modules and a single registration entrypoint."""

from __future__ import annotations

from ..app import Container
from . import planning, routes, stations, trains

__all__ = ["register_all"]


def register_all(mcp, container: Container) -> None:
    """Register every tool on the given FastMCP instance."""
    stations.register(mcp, container)
    trains.register(mcp, container)
    routes.register(mcp, container)
    planning.register(mcp, container)
