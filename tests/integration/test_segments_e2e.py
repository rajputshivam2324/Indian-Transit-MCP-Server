"""End-to-end tests for the find_best_booking_segment MCP tool.

The whole stack is wired (tool -> service -> search/route services -> providers) over
in-memory fakes loaded with the frozen 15708 / 12204 data, and called through FastMCP's
``call_tool`` exactly as a client would.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from tests.segment_fixtures import DATE, EXISTING_TOOLS, make_container
from transit_mcp.config import Settings
from transit_mcp.infra.http import HttpClient
from transit_mcp.server.app import build_container
from transit_mcp.server.main import INSTRUCTIONS, create_app
from transit_mcp.services.segments import BookingSegmentService


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


@contextlib.asynccontextmanager
async def _app(*, with_segments: bool = True):
    container = make_container(with_segments=with_segments)
    mcp, _ = create_app(settings=container.settings, container=container)
    try:
        yield mcp
    finally:
        await container.aclose()


async def _call(mcp, **args) -> dict:
    return _envelope(await mcp.call_tool("find_best_booking_segment", args))


async def test_tool_is_registered_alongside_every_existing_tool():
    async with _app() as mcp:
        tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == EXISTING_TOOLS | {"find_best_booking_segment"}

    tool = tools["find_best_booking_segment"]
    assert tool.annotations.readOnlyHint is True
    schema = tool.inputSchema
    assert schema["required"] == ["origin", "destination", "date"]
    props = schema["properties"]
    assert set(props) == {
        "origin",
        "destination",
        "date",
        "train_number",
        "travel_class",
        "quota",
        "max_extra_stations_each_side",
        "max_candidates",
        "min_gain_pct",
    }
    assert props["max_extra_stations_each_side"]["default"] == 5
    assert props["max_candidates"]["default"] == 6
    assert props["min_gain_pct"]["default"] == 10
    assert props["quota"]["default"] == "GN"
    assert "find_best_booking_segment" in INSTRUCTIONS  # the server tells clients when to use it


@pytest.mark.parametrize("with_segments", [True, False], ids=["wired", "built-on-demand"])
async def test_tool_returns_the_15708_suggestion_in_the_envelope(with_segments):
    async with _app(with_segments=with_segments) as mcp:
        env = await _call(
            mcp,
            origin="DLI",
            destination="MFP",
            date=DATE,
            train_number="15708",
            travel_class="3A",
        )

    assert env["ok"] is True
    data, meta = env["data"], env["meta"]
    assert (data["origin"], data["destination"], data["date"]) == ("DLI", "MFP", DATE)

    train = data["trains"][0]
    by_pair = {(s["booking_from"], s["booking_to"]): s for s in train["suggestions"]}
    s = by_pair[("ASR", "MFP")]
    assert (s["status"], s["confirm_chance"], s["fare"], s["quota"]) == (
        "GNWL34/WL19",
        87,
        1735,
        "GN",
    )
    assert (s["gain_pct"], s["extra_fare"], s["extra_km"]) == (15, 315, 447)
    assert s["departure_date"] == "10-10-2026"
    assert s["instruction"] == "Book ASR->MFP, set boarding at DLI in IRCTC, alight at MFP"
    assert train["user_leg"]["fare"] == 1420 and train["user_leg"]["confirm_chance"] == 72
    assert [p["code"] for p in train["route_proof"]][0] == "ASR"
    assert any("Boarding change required" in w for w in train["warnings"])
    assert "reason" not in train  # compaction: absent, not null

    assert data["best"]["train_number"] == "15708"
    assert data["best"]["suggestion"] == train["suggestions"][0]

    assert meta["trains_analyzed"] == 1 and meta["suggestions"] == 3
    assert meta["guidance"] and all(isinstance(step, str) and step for step in meta["guidance"])
    assert "15708" in meta["summary"] and "Book ASR->KIR" in meta["summary"]
    assert any("get_seat_availability" in a for a in meta["next_actions"])


async def test_tool_reports_a_wrong_direction_trip_as_an_empty_result_with_reason():
    async with _app() as mcp:
        env = await _call(mcp, origin="MFP", destination="DLI", date=DATE, train_number="15708")

    assert env["ok"] is True
    train = env["data"]["trains"][0]
    assert train["suggestions"] == []
    assert train["reason"] == "not on route / wrong direction"
    assert "user_leg" not in train and "detail" in train
    assert "best" not in env["data"]
    assert env["data"]["reason"].startswith("No suggestions for train 15708")
    assert env["meta"]["suggestions"] == 0
    assert any("get_train_route" in a for a in env["meta"]["next_actions"])


async def test_tool_reports_a_non_halt_as_a_reason():
    async with _app() as mcp:
        env = await _call(mcp, origin="GZB", destination="MFP", date=DATE, train_number="15708")
    train = env["data"]["trains"][0]
    assert train["suggestions"] == [] and "not a scheduled halt" in train["reason"]
    assert "GZB" in train["detail"]


async def test_tool_analyses_the_corridor_when_no_train_is_pinned():
    async with _app() as mcp:
        env = await _call(mcp, origin="DLI", destination="MFP", date=DATE, travel_class="3A")

    assert env["ok"] is True
    assert [t["train_number"] for t in env["data"]["trains"]] == ["15708", "12204"]
    assert env["meta"]["trains_matched"] == 2 and env["meta"]["trains_analyzed"] == 2
    assert env["data"]["best"]["train_number"] == "15708"


async def test_tool_honours_min_gain_pct_and_date_formats():
    async with _app() as mcp:
        env = await _call(
            mcp,
            origin="DLI",
            destination="MFP",
            date="2026-10-10",
            train_number="12204",
            min_gain_pct=5,
        )
    assert env["data"]["date"] == "10-10-2026"  # YYYY-MM-DD normalized
    pairs = [(s["booking_from"], s["booking_to"]) for s in env["data"]["trains"][0]["suggestions"]]
    assert ("ASR", "SHC") in pairs  # a 5-point gain, visible once the threshold allows it


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"quota": "TQ"}, "INVALID_INPUT"),
        ({"max_candidates": 0}, "INVALID_INPUT"),
        ({"date": "31/02/2026"}, "INVALID_DATE"),
    ],
)
async def test_tool_turns_bad_input_into_an_error_envelope(args, code):
    base = {"origin": "DLI", "destination": "MFP", "date": DATE, "train_number": "15708"}
    async with _app() as mcp:
        env = await _call(mcp, **{**base, **args})
    assert env["ok"] is False and env["error"]["code"] == code


async def test_build_container_wires_the_segment_service():
    container = build_container(
        Settings(confirmtkt_base_url="https://api.test"),
        http=HttpClient("https://api.test", max_retries=0),
    )
    try:
        assert isinstance(container.segments, BookingSegmentService)
    finally:
        await container.aclose()
