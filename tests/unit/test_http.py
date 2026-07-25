"""Tests for the HTTP client's retry/backoff and error normalization."""

from __future__ import annotations

import httpx
import pytest
import respx

from transit_mcp.infra.errors import UpstreamError, UpstreamUnavailableError
from transit_mcp.infra.http import HttpClient

BASE = "https://api.test"


def _client() -> HttpClient:
    # backoff=0 keeps tests instant.
    return HttpClient(BASE, max_retries=2, backoff=0.0)


@respx.mock
async def test_get_json_success():
    respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(200, json={"pong": True}))
    client = _client()
    try:
        assert await client.get_json("/ping") == {"pong": True}
    finally:
        await client.aclose()


@respx.mock
async def test_retries_5xx_then_succeeds():
    route = respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    client = _client()
    try:
        assert await client.get_json("/x") == {"ok": 1}
        assert route.call_count == 2
    finally:
        await client.aclose()


@respx.mock
async def test_5xx_exhausted_raises_unavailable():
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(500))
    client = _client()
    try:
        with pytest.raises(UpstreamUnavailableError):
            await client.get_json("/x")
    finally:
        await client.aclose()


@respx.mock
async def test_4xx_raises_upstream_error_without_retry():
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(404))
    client = _client()
    try:
        with pytest.raises(UpstreamError):
            await client.get_json("/x")
        assert route.call_count == 1  # 4xx is terminal, not retried
    finally:
        await client.aclose()


@respx.mock
async def test_timeout_then_success():
    route = respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.ConnectTimeout("boom"), httpx.Response(200, json={"ok": 1})]
    )
    client = _client()
    try:
        assert await client.get_json("/x") == {"ok": 1}
        assert route.call_count == 2
    finally:
        await client.aclose()


@respx.mock
async def test_drops_none_params():
    route = respx.get(f"{BASE}/s").mock(return_value=httpx.Response(200, json=[]))
    client = _client()
    try:
        await client.get_json("/s", {"a": "1", "b": None})
        assert "b" not in route.calls.last.request.url.params
        assert route.calls.last.request.url.params["a"] == "1"
    finally:
        await client.aclose()
