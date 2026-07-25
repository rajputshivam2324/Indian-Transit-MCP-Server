"""End-to-end wiring test: full stack (HTTP -> provider -> mapper -> service -> tool)
against respx-mocked fixtures, exercised both via the container and via MCP call_tool.
"""

from __future__ import annotations

import json

import httpx
import respx

from transit_mcp.config import Settings
from transit_mcp.infra.http import HttpClient
from transit_mcp.models.domain import TrainSearchResult
from transit_mcp.providers.estimators import PassthroughConfirmationEstimator
from transit_mcp.server.app import Container, build_container
from transit_mcp.server.main import create_app
from transit_mcp.services.planning import MultiModalService, SplitJourneyService
from transit_mcp.services.routes import NearbyStationService, RouteService
from transit_mcp.services.stations import StationService
from transit_mcp.services.trains import (
    AvailabilityService,
    ConfirmationService,
    TrainSearchService,
)

from tests.conftest import load_fixture
from tests.fakes import (
    FakeRouteProvider,
    FakeStationProvider,
    FakeTrainProvider,
    make_avail,
    make_route,
    make_train,
)

_SPLIT_DATE = "28-07-2026"

BASE = "https://api.test"

_MMCT_SUGGEST = {
    "data": {
        "stationList": [
            {
                "stationCode": "MMCT",
                "stationName": "Mumbai Central",
                "airportCode": "BOM",
                "city": "Mumbai",
                "state": "Maharashtra",
                "majorStn": True,
                "latitude": "18.97",
                "longitude": "72.82",
            }
        ]
    }
}


def _mock_endpoints() -> None:
    respx.get(url__regex=rf"{BASE}/api/v2/trains/stations/auto-suggestion.*", params={"searchString": "NDLS"}).mock(
        return_value=httpx.Response(200, json=load_fixture("autosuggest_ndls.json"))
    )
    respx.get(url__regex=rf"{BASE}/api/v2/trains/stations/auto-suggestion.*", params={"searchString": "MMCT"}).mock(
        return_value=httpx.Response(200, json=_MMCT_SUGGEST)
    )
    respx.get(url__regex=rf"{BASE}/api/v1/trains/search.*").mock(
        return_value=httpx.Response(200, json=load_fixture("search_ndls_mmct.json"))
    )
    respx.get(url__regex=rf"{BASE}/api/v1/trains/schedule.*").mock(
        return_value=httpx.Response(200, json=load_fixture("schedule_12951.json"))
    )


def _build():
    settings = Settings(confirmtkt_base_url=BASE)
    http = HttpClient(
        BASE,
        headers={"clientid": "ct-web", "apikey": "ct-web!2$", "deviceid": "dev-1"},
        max_retries=0,
        backoff=0.0,
    )
    container = build_container(settings, http=http)
    mcp, _ = create_app(settings=settings, container=container)
    return mcp, container


def _envelope(res) -> dict:
    """Extract the JSON envelope from a FastMCP call_tool result of any shape."""
    if isinstance(res, dict):
        return res
    if isinstance(res, tuple):
        for part in res:
            if isinstance(part, dict) and "ok" in part:
                return part
            if isinstance(part, (list, tuple)) and part:
                text = getattr(part[0], "text", None)
                if text:
                    return json.loads(text)
    seq = res if isinstance(res, (list, tuple)) else [res]
    for block in seq:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"could not extract envelope from {type(res)}")


@respx.mock
async def test_container_full_stack_search_and_route():
    _mock_endpoints()
    mcp, container = _build()
    try:
        resp = await container.train_search.search("NDLS", "MMCT", "24-07-2026")
        assert resp.origin == "NDLS" and resp.destination == "MMCT"
        assert resp.trains
        assert resp.trains[0].availability

        route = await container.routes.get_route("12951")
        assert route.from_code == "MMCT" and route.to_code == "NDLS"
        assert len(route.stops) > 100
    finally:
        await container.aclose()


@respx.mock
async def test_mcp_call_tool_search_trains_envelope():
    _mock_endpoints()
    mcp, container = _build()
    try:
        res = await mcp.call_tool(
            "search_trains",
            {
                "origin": "NDLS",
                "destination": "MMCT",
                "date": "24-07-2026",
                "sort_by": "duration",
                "limit": 3,
            },
        )
        env = _envelope(res)
        assert env["ok"] is True
        assert len(env["data"]["trains"]) <= 3
        assert env["meta"]["total_matched"] >= 1
        assert env["meta"]["guidance"]  # CoT scaffold rides in meta
    finally:
        await container.aclose()


@respx.mock
async def test_mcp_call_tool_find_station_code():
    _mock_endpoints()
    mcp, container = _build()
    try:
        res = await mcp.call_tool("find_station_code", {"query": "NDLS"})
        env = _envelope(res)
        assert env["ok"] is True
        assert env["data"]["resolved"]["code"] == "NDLS"
    finally:
        await container.aclose()


@respx.mock
async def test_mcp_call_tool_search_buses_graceful_when_disabled():
    _mock_endpoints()
    mcp, container = _build()
    try:
        res = await mcp.call_tool(
            "search_buses", {"origin": "NDLS", "destination": "MMCT", "date": "24-07-2026"}
        )
        env = _envelope(res)
        assert env["ok"] is True
        assert env["data"] == []
        assert env["meta"]["available"] is False
    finally:
        await container.aclose()


def _fake_split_container() -> Container:
    """Wire a full Container from in-memory fakes (no network) for a split journey
    where leg 1 arrives BPL and leg 2 departs RKMP (same-city station change)."""
    baseline = TrainSearchResult(
        source_code="AAA", destination_code="CCC", date=_SPLIT_DATE,
        trains=[make_train("900", from_code="AAA", to_code="CCC", distance=1000)],
    )
    leg1 = TrainSearchResult(
        source_code="AAA", destination_code="BPL", date=_SPLIT_DATE,
        trains=[make_train(
            "L1", from_code="AAA", to_code="BPL",
            departure="08:00", duration_min=240,
            availability=[make_avail("3A", fare=1000, chance=95)],
        )],
    )
    leg2 = TrainSearchResult(
        source_code="BPL", destination_code="CCC", date=_SPLIT_DATE,
        trains=[make_train(
            "L2", from_code="RKMP", to_code="CCC",
            departure="13:00", duration_min=300,
            availability=[make_avail("3A", fare=1200, chance=95)],
        )],
    )
    train_provider = FakeTrainProvider({
        ("AAA", "CCC", _SPLIT_DATE): baseline,
        ("AAA", "BPL", _SPLIT_DATE): leg1,
        ("BPL", "CCC", _SPLIT_DATE): leg2,
    })
    route_provider = FakeRouteProvider({
        "900": make_route("900", [
            ("AAA", 1, 0.0, True),
            ("BPL", 1, 400.0, True),
            ("CCC", 1, 1000.0, True),
        ]),
    })
    stations = StationService(FakeStationProvider())
    train_search = TrainSearchService(train_provider, stations)
    availability = AvailabilityService(train_search)
    confirmation = ConfirmationService(availability, PassthroughConfirmationEstimator())
    routes = RouteService(route_provider)
    split = SplitJourneyService(stations, train_search, routes)
    return Container(
        settings=Settings(),
        http=HttpClient("https://unused.test", max_retries=0),
        stations=stations,
        train_search=train_search,
        availability=availability,
        confirmation=confirmation,
        routes=routes,
        nearby=NearbyStationService(stations),
        split=split,
        multimodal=MultiModalService(train_search),
    )


async def test_split_journey_tool_returns_cot_guidance_and_flags():
    container = _fake_split_container()
    mcp, _ = create_app(settings=container.settings, container=container)
    try:
        res = await mcp.call_tool(
            "plan_split_journey",
            {"origin": "AAA", "destination": "CCC", "date": _SPLIT_DATE},
        )
        env = _envelope(res)
        assert env["ok"] is True

        # Chain-of-thought scaffold rides in meta so smaller models see it inline.
        guidance = env["meta"]["guidance"]
        assert isinstance(guidance, list) and len(guidance) >= 4
        assert all(isinstance(step, str) and step for step in guidance)

        # The station-change facts the client must reason over are pre-computed.
        assert env["data"], "expected at least one itinerary"
        it = env["data"][0]
        assert it["requires_station_change"] is True
        assert it["transfer_arrival_station"] == "BPL"
        assert it["transfer_departure_station"] == "RKMP"
        assert any("station change" in w.lower() for w in it["warnings"])

        # Pre-rendered breakdown so a weak model can't drop the layover or leg detail.
        disp = it["display"]
        assert "Layover:" in disp
        assert "L1" in disp and "L2" in disp  # both legs shown
        assert "BPL" in disp and "RKMP" in disp  # the station change is visible
    finally:
        await container.aclose()


@respx.mock
async def test_get_train_route_major_view_is_lossless_by_default():
    _mock_endpoints()
    mcp, container = _build()
    try:
        # Default (all) returns every stop; exclude_none drops null fields.
        full = _envelope(await mcp.call_tool("get_train_route", {"train_number": "12951"}))
        assert full["ok"] is True
        total = full["meta"]["total_stops"]
        assert total > 100
        assert len(full["data"]["stops"]) == total  # nothing dropped by default
        # Compaction: a non-major stop with no platform must omit the null key.
        minor = next(s for s in full["data"]["stops"] if not s.get("is_major"))
        assert "platform" not in minor  # exclude_none removed the null

        # Opt-in major view is smaller but reports the true total.
        major = _envelope(
            await mcp.call_tool("get_train_route", {"train_number": "12951", "stops": "major"})
        )
        assert major["meta"]["total_stops"] == total
        assert major["meta"]["shown_stops"] < total
        assert all(s["is_major"] for s in major["data"]["stops"])
    finally:
        await container.aclose()


@respx.mock
async def test_search_trains_includes_summary():
    _mock_endpoints()
    mcp, container = _build()
    try:
        env = _envelope(
            await mcp.call_tool(
                "search_trains",
                {"origin": "NDLS", "destination": "MMCT", "date": "24-07-2026"},
            )
        )
        assert env["ok"] is True
        assert env["meta"]["summary"]  # server-authored synthesis present
        assert "trains" in env["meta"]["summary"].lower()
    finally:
        await container.aclose()
