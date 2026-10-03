"""MCP server entrypoint.

``create_app`` builds a wired :class:`FastMCP` (plus its :class:`Container`).
``main`` serves it over streamable HTTP (the default) or stdio, as ``Settings.transport``
says, and closes the shared HTTP client on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import __version__
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
    "single-transfer options; plan_trip to compare modes; find_best_booking_segment when "
    "a train is waitlisted (a longer booking on the same train that confirms more easily, "
    "then change the boarding point).\n\n"
    "Two conventions in meta carry the reasoning:\n"
    "- meta.guidance: an ordered, tool-specific checklist. Follow it step by step before "
    "you answer.\n"
    "- meta.next_actions: the exact follow-up tool(s) to call when a result is empty, "
    "fully waitlisted, or truncated. Act on them before concluding.\n\n"
    "Completeness contract: report every train and every class returned (never drop "
    "options silently), distinguish AVAILABLE/RAC/WL and General/Tatkal, surface all "
    "warnings, and state explicitly whatever was unavailable or truncated."
)


# Binds that FastMCP treats as loopback, and the Host / Origin values it then allows by
# default (its automatic DNS-rebinding protection; see mcp.server.fastmcp.FastMCP.__init__).
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_LOOPBACK_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOOPBACK_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def transport_security_for(s: Settings) -> TransportSecuritySettings | None:
    """DNS-rebinding settings for the HTTP transport, or ``None`` to keep the SDK default.

    The SDK default already protects a loopback bind. Configured hosts/origins extend it, so
    adding a public hostname does not lock localhost out. On any other bind they are the only
    thing that switches the protection on.
    """
    hosts, origins = s.allowed_host_list, s.allowed_origin_list
    if not hosts and not origins:
        return None
    if s.server_host in _LOOPBACK_HOSTS:
        hosts = [*_LOOPBACK_ALLOWED_HOSTS, *hosts]
        origins = [*_LOOPBACK_ALLOWED_ORIGINS, *origins]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


def _register_health_routes(mcp: FastMCP, s: Settings) -> None:
    """Expose ``/`` and ``/health`` so platform probes and browsers don't hit a bare 404.

    MCP itself lives at ``s.server_path`` (default ``/mcp``). Render's default health check
    and anyone opening the service URL hit ``/``, which FastMCP does not serve on its own.
    """

    async def health(_request: Request) -> Response:
        return JSONResponse(
            {
                "ok": True,
                "service": "indian-transit",
                "version": __version__,
                "transport": "streamable-http",
                "mcp": s.server_path,
            }
        )

    mcp.custom_route("/", methods=["GET", "HEAD"])(health)
    mcp.custom_route("/health", methods=["GET", "HEAD"])(health)


def create_app(
    settings: Settings | None = None,
    container: Container | None = None,
) -> tuple[FastMCP, Container]:
    """Build a FastMCP server with all tools registered and return it + its container.

    The HTTP settings are passed to the constructor on purpose: FastMCP only switches its
    automatic DNS-rebinding protection on for a loopback ``host`` while it is being built.
    """
    s = settings or get_settings()
    configure_logging(s.log_level)
    c = container or build_container(s)
    mcp = FastMCP(
        name="indian-transit",
        instructions=INSTRUCTIONS,
        host=s.server_host,
        port=s.server_port,
        streamable_http_path=s.server_path,
        transport_security=transport_security_for(s),
    )
    register_all(mcp, c)
    _register_health_routes(mcp, s)
    return mcp, c


def listen_url(s: Settings) -> str:
    host = f"[{s.server_host}]" if ":" in s.server_host else s.server_host
    return f"http://{host}:{s.server_port}{s.server_path}"


def _announce_http(s: Settings) -> None:
    log.info("serving MCP over streamable HTTP at %s", listen_url(s))
    if s.server_host in _LOOPBACK_HOSTS:
        return
    log.warning(
        "listening on %s, which is not loopback, and this server has NO authentication: anyone "
        "who can reach %s can call every tool and spend your upstream quota. Restrict access "
        "with a firewall or an authenticating reverse proxy.",
        s.server_host,
        listen_url(s),
    )
    if not (s.allowed_host_list or s.allowed_origin_list):
        log.warning(
            "DNS-rebinding protection is OFF for this bind address; set TRANSIT_ALLOWED_HOSTS "
            "(and TRANSIT_ALLOWED_ORIGINS) to switch it on."
        )


def main() -> None:
    """Console-script entrypoint: serve over streamable HTTP (default) or stdio."""
    s = get_settings()
    mcp, container = create_app(s)
    log.info(
        "starting indian-transit MCP server (transport=%s bus=%s flight=%s)",
        s.transport,
        s.enable_bus,
        s.enable_flight,
    )
    if s.transport == "streamable-http":
        _announce_http(s)
    try:
        mcp.run(transport=s.transport)
    except KeyboardInterrupt:
        # Ctrl+C: uvicorn has already finished a graceful shutdown by the time this lands.
        log.info("interrupted, shut down")
    finally:
        with contextlib.suppress(Exception):
            asyncio.run(container.aclose())


if __name__ == "__main__":
    main()
