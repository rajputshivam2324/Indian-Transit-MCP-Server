"""End-to-end tests over real streamable HTTP.

A uvicorn server is started in-process on an ephemeral loopback port and driven with the real
MCP client (and plain HTTP where headers matter), so sessions, SSE framing, path routing and
the Host / Origin checks all run for real. Upstream data is the in-memory 15708 / 12204 fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx
import uvicorn
from mcp import ClientSession

from tests.segment_fixtures import DATE, EXISTING_TOOLS, make_container
from transit_mcp.config import Settings
from transit_mcp.server.main import create_app

try:
    from mcp.client.streamable_http import streamable_http_client as connect
except ImportError:  # older mcp releases only ship the (now deprecated) original name
    from mcp.client.streamable_http import streamablehttp_client as connect

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}
JSON_AND_SSE = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
ALL_TOOLS = EXISTING_TOOLS | {"find_best_booking_segment"}


@contextlib.asynccontextmanager
async def serve(**settings):
    """Run the real app on an ephemeral loopback port; yield its base URL."""
    container = make_container()
    mcp, _ = create_app(Settings(_env_file=None, **settings), container)
    server = uvicorn.Server(
        uvicorn.Config(
            mcp.streamable_http_app(),
            host="127.0.0.1",
            port=0,
            log_level="warning",
            timeout_graceful_shutdown=2,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError("uvicorn exited before it started")
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        await task
        await container.aclose()


async def _post_initialize(url: str, **headers) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(url, json=INITIALIZE, headers={**JSON_AND_SSE, **headers})


async def _tool_names(url: str) -> set[str]:
    async with connect(url) as (read, write, _), ClientSession(read, write) as session:
        await session.initialize()
        return {t.name for t in (await session.list_tools()).tools}


async def test_a_real_client_lists_and_calls_tools_over_streamable_http():
    async with asyncio.timeout(30), serve() as base:
        async with connect(f"{base}/mcp") as (read, write, get_session_id):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "indian-transit"
                assert "find_best_booking_segment" in init.instructions
                assert get_session_id()  # streamable HTTP hands out a session id

                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == ALL_TOOLS

                found = await session.call_tool(
                    "find_best_booking_segment",
                    {
                        "origin": "DLI",
                        "destination": "MFP",
                        "date": DATE,
                        "train_number": "15708",
                        "travel_class": "3A",
                    },
                )
                env = json.loads(found.content[0].text)
                assert env["ok"] is True
                pairs = {
                    (s["booking_from"], s["booking_to"])
                    for s in env["data"]["trains"][0]["suggestions"]
                }
                assert ("ASR", "MFP") in pairs

                # The tools that existed before the transport change behave as they did.
                searched = await session.call_tool(
                    "search_trains", {"origin": "DLI", "destination": "MFP", "date": DATE}
                )
                assert json.loads(searched.content[0].text)["meta"]["total_matched"] == 2


async def test_the_configured_path_is_served_and_the_default_one_is_not():
    async with asyncio.timeout(30), serve(server_path="/transit/mcp") as base:
        assert await _tool_names(f"{base}/transit/mcp") == ALL_TOOLS
        assert (await _post_initialize(f"{base}/mcp")).status_code == 404


async def test_root_and_health_answer_platform_probes():
    """Render hits ``/`` with HEAD/GET; browsers open the service URL the same way."""
    async with asyncio.timeout(30), serve(server_path="/transit/mcp") as base:
        async with httpx.AsyncClient() as client:
            for path in ("/", "/health"):
                get = await client.get(f"{base}{path}")
                assert get.status_code == 200
                body = get.json()
                assert body["ok"] is True and body["mcp"] == "/transit/mcp"
                assert body["service"] == "indian-transit"

                head = await client.head(f"{base}{path}")
                assert head.status_code == 200


async def test_a_foreign_host_header_is_refused_but_local_names_work():
    async with asyncio.timeout(30), serve() as base:
        url = f"{base}/mcp"
        ok = await _post_initialize(url)
        assert ok.status_code == 200 and ok.headers.get("mcp-session-id")
        assert (await _post_initialize(url, Host="localhost:4321")).status_code == 200

        refused = await _post_initialize(url, Host="evil.example")
        assert refused.status_code == 421 and "Invalid Host header" in refused.text


async def test_a_foreign_origin_is_refused_but_local_pages_are_allowed():
    async with asyncio.timeout(30), serve() as base:
        url = f"{base}/mcp"
        assert (await _post_initialize(url, Origin="http://localhost:3000")).status_code == 200

        refused = await _post_initialize(url, Origin="http://evil.example")
        assert refused.status_code == 403 and "Invalid Origin header" in refused.text


async def test_allowed_hosts_admit_a_public_hostname_and_nothing_else():
    async with asyncio.timeout(30), serve(allowed_hosts="mcp.example.com") as base:
        url = f"{base}/mcp"
        assert (await _post_initialize(url, Host="mcp.example.com")).status_code == 200
        assert (await _post_initialize(url)).status_code == 200  # loopback still works
        assert (await _post_initialize(url, Host="other.example")).status_code == 421
