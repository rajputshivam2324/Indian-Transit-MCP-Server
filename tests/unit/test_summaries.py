"""Tests for deterministic server-authored summaries (meta.summary)."""

from __future__ import annotations

from transit_mcp.models.domain import (
    JourneyLeg,
    SplitItinerary,
    TripOption,
)
from transit_mcp.models.enums import TransportMode
from transit_mcp.server.tools._summaries import (
    safe,
    summarize_confirmation,
    summarize_search,
    summarize_split,
    summarize_trip,
)
from transit_mcp.services.trains import ConfirmationResult, SearchResponse

from tests.fakes import make_avail, make_train


def test_safe_swallows_errors_and_empty():
    assert safe(lambda: (_ for _ in ()).throw(ValueError())) is None
    assert safe(lambda: "") is None
    assert safe(lambda: "ok") == "ok"


def test_summarize_search_reports_fastest_cheapest_confirmation_waitlist():
    trains = [
        make_train("111", duration_min=480,
                   availability=[make_avail("SL", fare=500, chance=90, seats=10, status="AVL 10")]),
        make_train("222", duration_min=840,
                   availability=[make_avail("SL", fare=300, chance=20, seats=None, status="WL 5")]),
        make_train("333", duration_min=120,
                   availability=[make_avail("2A", fare=2000, chance=99, seats=5, status="AVL 5")]),
    ]
    resp = SearchResponse(
        origin="MFP", destination="NDLS", date="24-07-2026", total_matched=8, trains=trains
    )
    s = summarize_search(resp)
    assert "3 of 8" in s
    assert "333" in s  # fastest (120 min) and best confirmation (99%)
    assert "Rs 300" in s  # cheapest
    assert "1 of 3 are fully waitlisted" in s  # train 222 has no open seat


def test_summarize_search_empty():
    resp = SearchResponse(origin="A", destination="B", date="24-07-2026", total_matched=0, trains=[])
    assert "No direct trains" in summarize_search(resp)


def test_summarize_split_top_option():
    it = SplitItinerary(
        legs=[
            JourneyLeg(train=make_train("L1"), from_code="A", to_code="H",
                       availability=make_avail("3A", fare=1000, chance=95)),
            JourneyLeg(train=make_train("L2"), from_code="H", to_code="B",
                       availability=make_avail("3A", fare=1500, chance=90)),
        ],
        transfer_station="H", travel_class="3A", total_duration_min=600,
        layover_min=45, total_fare=2500, combined_confirm_chance=85.5,
    )
    s = summarize_split([it])
    assert "via H" in s and "L1 + L2" in s and "3A" in s
    assert "Rs 2500" in s and "45-min" in s


def test_summarize_confirmation_flags_low_chance():
    high = ConfirmationResult(
        train_number="12951", origin="MMCT", destination="NDLS", date="24-07-2026",
        travel_class="3A", quota="GN", confirm_chance=83, confirm_status="Confirm",
        booking_status="WL 17", fare=1650,
    )
    s = summarize_confirmation(high)
    assert "12951" in s and "83%" in s and "WL 17" in s

    low = high.model_copy(update={"confirm_chance": 35})
    assert "backup" in summarize_confirmation(low).lower()


def test_summarize_trip_notes_unavailable_modes():
    opts = [TripOption(mode=TransportMode.TRAIN, summary="12952 Tejas", total_duration_min=883,
                       price=3000, confirm_chance=100.0)]
    meta = {
        "train": {"available": True, "count": 1},
        "bus": {"available": False, "reason": "disabled"},
        "flight": {"available": False, "reason": "disabled"},
    }
    s = summarize_trip(opts, meta)
    assert "1 option" in s
    assert "bus" in s.lower() and "flight" in s.lower()
