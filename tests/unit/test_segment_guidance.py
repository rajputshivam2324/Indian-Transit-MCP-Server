"""Tests for the booking-segment CoT scaffold, ReAct next actions, and summary line."""

from __future__ import annotations

from tests.segment_fixtures import DATE, service_12204, service_15708
from transit_mcp.models.segments import BookingSegmentReport, TrainSegmentResult
from transit_mcp.server.tools._guidance import BOOKING_SEGMENT_GUIDANCE, segment_next_actions
from transit_mcp.server.tools._summaries import safe, summarize_segments
from transit_mcp.services.segments import REASON_NO_GAIN, REASON_NOT_ON_ROUTE


def _report(trains, **kw) -> BookingSegmentReport:
    base = dict(
        origin="DLI",
        destination="MFP",
        date=DATE,
        min_gain_pct=10,
        max_extra_stations_each_side=5,
        max_candidates=6,
        trains_matched=len(trains),
        trains=trains,
    )
    base.update(kw)
    return BookingSegmentReport(**base)


def test_guidance_is_an_ordered_checklist_of_nonempty_steps():
    assert len(BOOKING_SEGMENT_GUIDANCE) >= 5
    assert all(isinstance(s, str) and s for s in BOOKING_SEGMENT_GUIDANCE)
    text = " ".join(BOOKING_SEGMENT_GUIDANCE)
    # The points a weak model is most likely to drop.
    assert "confirmed or RAC" in text  # boarding change is impossible while waitlisted
    assert "departure_date" in text and "EARLIER" in text  # the date shift
    assert "warnings" in text and "instruction" in text
    assert "get_seat_availability" in text


async def test_summary_leads_with_the_best_suggestion():
    svc, _ = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    summary = summarize_segments(report)
    assert summary.startswith("Best: train 15708 - 3A ASR->KIR GNWL34/WL19 88% vs RLWL21/WL13 72%")
    assert "+16 points, +Rs 540" in summary
    assert "Book for 10-10-2026 departing ASR 07:40." in summary
    assert "Book ASR->KIR, set boarding at DLI in IRCTC, alight at MFP." in summary
    assert summary.endswith("3 suggestion(s) on 1 of 1 train(s) analysed.")


async def test_summary_without_a_suggestion_states_why():
    svc, _ = service_12204()
    report = await svc.find("MFP", "DLI", DATE, train_number="12204")
    assert summarize_segments(report) == (
        "No suggestions for train 12204: not on route / wrong direction."
    )
    assert safe(summarize_segments, _report([])) == "No booking segment found."


async def test_next_actions_after_a_result_point_to_rechecking_the_pair():
    svc, _ = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    actions = segment_next_actions(report)
    assert len(actions) == 1
    assert "re-check ASR->KIR on 10-10-2026" in actions[0]
    assert (
        "get_seat_availability(train_number='15708', origin='ASR', destination='KIR'" in actions[0]
    )


def test_next_actions_when_nothing_matched_the_corridor():
    actions = segment_next_actions(_report([]))
    assert any("find_station_code" in a for a in actions)
    assert any("suggest_nearby_stations" in a for a in actions)
    assert any("plan_split_journey" in a for a in actions)


def test_next_actions_when_no_gain_was_found_suggest_the_right_knobs():
    train = TrainSegmentResult(
        train_number="12204",
        reason=REASON_NO_GAIN,
        candidates_considered=48,
        candidates_evaluated=6,
    )
    skipped = TrainSegmentResult(train_number="99999", reason=REASON_NOT_ON_ROUTE)
    actions = " ".join(segment_next_actions(_report([train, skipped])))
    assert "raise max_candidates or max_extra_stations_each_side" in actions
    assert "Lower min_gain_pct" in actions
    assert "get_train_route" in actions and "suggest_nearby_stations" in actions
    assert "search_trains" in actions


def test_next_actions_do_not_suggest_a_bigger_budget_once_everything_was_tried():
    train = TrainSegmentResult(
        train_number="1", reason=REASON_NO_GAIN, candidates_considered=3, candidates_evaluated=3
    )
    actions = " ".join(segment_next_actions(_report([train])))
    assert "max_candidates" not in actions and "Lower min_gain_pct" in actions


def test_next_actions_mention_trains_that_were_matched_but_not_analysed():
    train = TrainSegmentResult(train_number="1", reason=REASON_NO_GAIN)
    corridor = segment_next_actions(_report([train], trains_matched=14))
    assert any("13 more train(s) matched the corridor" in a for a in corridor)
    pinned = segment_next_actions(_report([train], trains_matched=14, train_number="1"))
    assert not any("more train(s)" in a for a in pinned)
