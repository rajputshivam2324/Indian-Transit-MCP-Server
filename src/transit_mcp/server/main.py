"""MCP stdio entrypoint.

``create_app`` builds a wired :class:`FastMCP` (plus its :class:`Container`).
``main`` runs it over stdio and closes the shared HTTP client on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib

from mcp.server.fastmcp import FastMCP

from ..config import Settings, get_settings
from ..infra.logging import configure_logging, get_logger
from .app import Container, build_container
from .tools import register_all

log = get_logger("server")

INSTRUCTIONS = (
    "Deterministic, read-only tools for Indian multi-modal transit search "
    "(trains primary; buses and flights when enabled). Every tool returns an envelope: "
    "{ok:true, data, meta} or {ok:false, error:{code,message}}.\n\n"
    "Work in a ReAct loop - Thought -> Action (call a tool) -> Observation -> repeat - "
    "and chain tools instead of guessing. Typical flow: resolve place names with "
    "find_station_code, then search_trains. For a specific train use get_seat_availability "
    "or predict_confirmation; get_train_route for stops; plan_split_journey for "
    "single-transfer options; plan_trip to compare modes.\n\n"
    "Two conventions in meta carry the reasoning:\n"
    "- meta.guidance: an ordered, tool-specific checklist. Follow it step by step before "
    "you answer.\n"
    "- meta.next_actions: the exact follow-up tool(s) to call when a result is empty, "
    "fully waitlisted, or truncated. Act on them before concluding.\n\n"
    "Completeness contract: report every train and every class returned (never drop "
    "options silently), distinguish AVAILABLE/RAC/WL and General/Tatkal, surface all "
    "warnings, and state explicitly whatever was unavailable or truncated."
)


def create_app(
    settings: Settings | None = None,
    container: Container | None = None,
) -> tuple[FastMCP, Container]:
    """Build a FastMCP server with all tools registered and return it + its container."""
    s = settings or get_settings()
    configure_logging(s.log_level)
    c = container or build_container(s)
    mcp = FastMCP(name="indian-transit", instructions=INSTRUCTIONS)
    register_all(mcp, c)
    return mcp, c


def main() -> None:
    """Console-script entrypoint: run the server over stdio."""
    mcp, container = create_app()
    log.info(
        "starting indian-transit MCP server (bus=%s flight=%s)",
        container.settings.enable_bus,
        container.settings.enable_flight,
    )
    try:
        mcp.run()
    finally:
        with contextlib.suppress(Exception):
            asyncio.run(container.aclose())


if __name__ == "__main__":
    main()
