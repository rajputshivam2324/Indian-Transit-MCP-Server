"""Provider tests: respx-mocked HTTP against real captured fixture payloads."""

from __future__ import annotations

import httpx
import pytest
import respx

from transit_mcp.infra.errors import NoResultsError
from transit_mcp.infra.http import HttpClient
from transit_mcp.providers.confirmtkt import (
    ConfirmTktRouteProvider,
    ConfirmTktStationProvider,
    ConfirmTktTrainProvider,
)

BASE = "https://api.test"


def _http() -> HttpClient:
    return HttpClient(BASE, max_retries=0, backoff=0.0)


@respx.mock
async def test_station_provider_maps_fixture(autosuggest_payload):
    respx.get(url__regex=rf"{BASE}/api/v2/trains/stations/auto-suggestion.*").mock(
        return_value=httpx.Response(200, json=autosuggest_payload)
    )
    http = _http()
    try:
        stations = await ConfirmTktStationProvider(http).autosuggest("NDLS")
    finally:
        await http.aclose()
    assert stations[0].code == "NDLS"
    assert any(s.airport_code == "DEL" for s in stations)


@respx.mock
async def test_train_provider_maps_fixture(search_payload):
    respx.get(url__regex=rf"{BASE}/api/v1/trains/search.*").mock(
        return_value=httpx.Response(200, json=search_payload)
    )
    http = _http()
    try:
        result = await ConfirmTktTrainProvider(http).search("NDLS", "MMCT", "24-07-2026")
    finally:
        await http.aclose()
    assert result.trains
    assert result.quotas
    assert result.trains[0].availability


@respx.mock
async def test_route_provider_maps_fixture(schedule_payload):
    respx.get(url__regex=rf"{BASE}/api/v1/trains/schedule.*").mock(
        return_value=httpx.Response(200, json=schedule_payload)
    )
    http = _http()
    try:
        route = await ConfirmTktRouteProvider(http).route("12951")
    finally:
        await http.aclose()
    assert route.number == "12951"
    assert len(route.stops) > 100
    assert route.stops[0].code == "MMCT"


@respx.mock
async def test_route_provider_empty_schedule_raises():
    respx.get(url__regex=rf"{BASE}/api/v1/trains/schedule.*").mock(
        return_value=httpx.Response(200, json={"Schedule": [], "ErrorMsg": "not found"})
    )
    http = _http()
    try:
        with pytest.raises(NoResultsError):
            await ConfirmTktRouteProvider(http).route("00000")
    finally:
        await http.aclose()


@respx.mock
async def test_train_provider_sends_required_headers_and_params(search_payload):
    route = respx.get(url__regex=rf"{BASE}/api/v1/trains/search.*").mock(
        return_value=httpx.Response(200, json=search_payload)
    )
    http = HttpClient(
        BASE,
        headers={"clientid": "ct-web", "apikey": "ct-web!2$", "deviceid": "dev-1"},
        max_retries=0,
    )
    try:
        await ConfirmTktTrainProvider(http).search("ndls", "mmct", "24-07-2026")
    finally:
        await http.aclose()
    req = route.calls.last.request
    assert req.headers["clientid"] == "ct-web"
    assert req.headers["apikey"] == "ct-web!2$"
    params = req.url.params
    assert params["sourceStationCode"] == "NDLS"  # upper-cased
    assert params["destinationStationCode"] == "MMCT"
    assert params["dateOfJourney"] == "24-07-2026"
