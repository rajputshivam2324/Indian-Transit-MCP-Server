"""Tests for the ReAct next-action logic and CoT scaffolds."""

from __future__ import annotations

from transit_mcp.server.tools._guidance import (
    SEARCH_TRAINS_GUIDANCE,
    search_next_actions,
)
from transit_mcp.services.trains import SearchResponse

from tests.fakes import make_avail, make_train


def _resp(trains, total_matched=None) -> SearchResponse:
    return SearchResponse(
        origin="AAA",
        destination="BBB",
        date="24-07-2026",
        total_matched=total_matched if total_matched is not None else len(trains),
        trains=trains,
    )


def test_guidance_lists_are_nonempty_strings():
    assert SEARCH_TRAINS_GUIDANCE
    assert all(isinstance(s, str) and s for s in SEARCH_TRAINS_GUIDANCE)


def test_next_actions_empty_results_suggests_recovery_tools():
    actions = search_next_actions(_resp([]))
    assert actions
    joined = " ".join(actions).lower()
    assert "find_station_code" in joined
    assert "suggest_nearby_stations" in joined
    assert "plan_split_journey" in joined


def test_next_actions_all_waitlisted_suggests_prediction():
    wl = make_train("111", availability=[make_avail("SL", chance=35, seats=None, status="WL 40")])
    actions = search_next_actions(_resp([wl]))
    assert any("predict_confirmation" in a for a in actions)


def test_next_actions_open_seat_no_waitlist_suggestion():
    ok_train = make_train(
        "222", availability=[make_avail("3A", chance=100, seats=120, status="AVL 120")]
    )
    actions = search_next_actions(_resp([ok_train]))
    assert not any("waitlisted" in a.lower() for a in actions)


def test_next_actions_flags_truncation():
    ok_train = make_train(
        "222", availability=[make_avail("3A", chance=100, seats=120, status="AVL 120")]
    )
    actions = search_next_actions(_resp([ok_train], total_matched=9))
    assert any("more matches" in a.lower() for a in actions)
