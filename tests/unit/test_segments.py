"""Unit tests for best-booking-segment: route analysis helpers and the full service.

Scenarios mirror real cases on 10-10-2026 (frozen in ``tests/segment_fixtures.py``):

* 15708 DLI->MFP 3A is RLWL 72% Rs1420, while ASR->MFP (the train's origin) is GNWL 87% Rs1735.
* 12204 DLI->MFP 3A is RLWL 74% Rs1015, while ASR->SHC / PGW->MFP are GNWL / PQWL at 79-86%.
"""

from __future__ import annotations

import pytest

from tests.fakes import make_avail, make_schedule_route
from tests.segment_fixtures import (
    DATE,
    PAIRS_15708,
    make_provider,
    make_service,
    pairs_12204,
    route_12204,
    route_15708,
    service_12204,
    service_15708,
)
from transit_mcp.infra.errors import InvalidInputError
from transit_mcp.models.domain import Stop
from transit_mcp.models.segments import SegmentSuggestion
from transit_mcp.services.segments import (
    REASON_BASELINE_STRONG,
    REASON_CLASS_NOT_OFFERED,
    REASON_INTERNAL,
    REASON_NO_BASELINE,
    REASON_NO_EXTENSION,
    REASON_NO_GAIN,
    REASON_NO_ROUTE,
    REASON_NOT_A_HALT,
    REASON_NOT_ON_ROUTE,
    _Skip,
    alighting_date,
    booking_date,
    build_route_stops,
    candidate_windows,
    departure_day,
    effective_halt_min,
    locate_user_stops,
    rank_candidate_pairs,
    suggestion_sort_key,
)


def _codes(stops, indices):
    return [stops[i].code for i in indices]


def _index(stops, code):
    return next(i for i, s in enumerate(stops) if s.code == code)


def _pairs(result):
    return [(s.booking_from, s.booking_to) for s in result.suggestions]


# --------------------------------------------------------------------------- #
# Route analysis helpers (pure)
# --------------------------------------------------------------------------- #
def _stop(**kw) -> Stop:
    base = dict(code="X", name="X", day=1)
    base.update(kw)
    return Stop(**base)


def test_effective_halt_uses_clock_times_when_halt_minutes_missing():
    # A real halting stop (Warangal on 12626) ships arrival 18:28, departure 18:30, no halt.
    assert effective_halt_min(_stop(arrival="18:28", departure="18:30")) == 2
    assert effective_halt_min(_stop(arrival="18:28", departure="18:28")) == 0  # pass-through
    assert effective_halt_min(_stop(arrival="23:58", departure="00:03")) == 5  # across midnight
    assert effective_halt_min(_stop(arrival=None, departure="07:40")) is None  # origin
    assert effective_halt_min(_stop(arrival="18:28", departure="18:30", halt_min=10)) == 10


def test_departure_day_rolls_over_when_a_halt_crosses_midnight():
    assert departure_day(_stop(arrival="23:50", departure="00:10", day=1)) == 2
    assert departure_day(_stop(arrival="10:00", departure="10:10", day=3)) == 3
    assert departure_day(_stop(arrival=None, departure="07:40", day=1)) == 1


def test_route_stop_flags_separate_halts_from_pass_throughs():
    stops = build_route_stops(route_15708())
    by = {s.code: s for s in stops}
    assert by["ASR"].is_halt and by["ASR"].is_candidate  # origin
    assert by["KIR"].is_halt and by["KIR"].is_candidate  # terminus
    assert by["DLI"].halt_min == 15 and by["DLI"].is_candidate
    assert not by["GZB"].is_halt and not by["GZB"].is_candidate  # runs through


def test_major_stop_without_a_halt_is_not_a_booking_point():
    rows = [
        ("AAA", "A", 1, 0.0, None, "08:00", True),
        ("BBB", "B", 1, 100.0, "09:00", "09:00", True),  # flagged major, but no halt
        ("CCC", "C", 1, 200.0, "10:00", "10:05", True),
        ("DDD", "D", 1, 300.0, "11:00", None, True),
    ]
    stops = build_route_stops(make_schedule_route("1", "T", rows))
    assert not stops[1].is_halt and not stops[1].is_candidate
    boards, _ = candidate_windows(stops, 2, 3, max_extra=5)
    assert _codes(stops, boards) == ["CCC", "AAA"]  # BBB is skipped


def test_locate_user_stops_requires_order_presence_and_halts():
    stops = build_route_stops(route_15708())
    i_o, i_d = locate_user_stops(stops, "DLI", "MFP")
    assert (stops[i_o].code, stops[i_d].code) == ("DLI", "MFP") and i_o < i_d

    with pytest.raises(_Skip) as reverse:
        locate_user_stops(stops, "MFP", "DLI")
    assert reverse.value.reason == REASON_NOT_ON_ROUTE

    with pytest.raises(_Skip) as missing:
        locate_user_stops(stops, "DLI", "XXX")
    assert missing.value.reason == REASON_NOT_ON_ROUTE and "XXX" in missing.value.detail

    with pytest.raises(_Skip) as not_halt:
        locate_user_stops(stops, "GZB", "MFP")
    assert not_halt.value.reason == REASON_NOT_A_HALT and "GZB" in not_halt.value.detail

    with pytest.raises(_Skip) as not_halt_dest:
        locate_user_stops(stops, "DLI", "GZB")
    assert not_halt_dest.value.reason == REASON_NOT_A_HALT
    assert "destination" in not_halt_dest.value.detail


def test_candidate_windows_count_halting_stations_not_route_rows():
    stops = build_route_stops(route_15708())
    i_o, i_d = _index(stops, "DLI"), _index(stops, "MFP")
    boards, alights = candidate_windows(stops, i_o, i_d, max_extra=5)
    # Nearest first, user's own station leading, then the train's origin/terminus.
    assert _codes(stops, boards) == ["DLI", "SZM", "SNP", "BDMJ", "PNP", "KUN", "ASR"]
    assert _codes(stops, alights) == ["MFP", "DOL", "KRBP", "SPJ", "BJU", "BGS", "KIR"]

    boards, alights = candidate_windows(stops, i_o, i_d, max_extra=0)
    assert _codes(stops, boards) == ["DLI", "ASR"]
    assert _codes(stops, alights) == ["MFP", "KIR"]

    # 12204: SZM is a pass-through there, so the window reaches one station further back.
    stops = build_route_stops(route_12204())
    boards, alights = candidate_windows(
        stops, _index(stops, "DLI"), _index(stops, "MFP"), max_extra=5
    )
    assert _codes(stops, boards) == ["DLI", "UMB", "DDL", "PGW", "JUC", "BEAS", "ASR"]
    assert _codes(stops, alights) == ["MFP", "SPJ", "DSS", "BJU", "BGS", "KGG", "SHC"]


def test_window_never_duplicates_endpoints_already_inside_it():
    stops = build_route_stops(route_15708())
    i_o, i_d = _index(stops, "SZM"), _index(stops, "MFP")  # ASR is within 9 stops, not 5
    boards, _ = candidate_windows(stops, i_o, i_d, max_extra=20)
    assert len(boards) == len(set(boards)) and boards[-1] == 0


def test_candidate_pairs_rank_train_origin_first_then_boarding_side_then_distance():
    stops = build_route_stops(route_15708())
    i_o, i_d = _index(stops, "DLI"), _index(stops, "MFP")
    boards, alights = candidate_windows(stops, i_o, i_d, max_extra=5)
    pairs = rank_candidate_pairs(stops, i_o, i_d, boards, alights)

    labels = [(stops[p.board].code, stops[p.alight].code) for p in pairs]
    assert len(pairs) == 7 * 7 - 1  # the user's own pair is excluded
    assert ("DLI", "MFP") not in labels
    assert labels[:2] == [("ASR", "MFP"), ("ASR", "KIR")]  # anchored at the train's origin
    assert labels[2:7] == [
        ("SZM", "MFP"),
        ("SNP", "MFP"),
        ("BDMJ", "MFP"),
        ("PNP", "MFP"),
        ("KUN", "MFP"),
    ]  # boarding side only, nearest first
    assert [p.tier for p in pairs[:7]] == [0, 0, 1, 1, 1, 1, 1]
    assert [p.extra_km for p in pairs[:3]] == [447.0, 729.0, 3.0]

    # Every pair extends the user leg in the travel direction, on at least one side.
    for p in pairs:
        assert p.board <= i_o and p.alight >= i_d and (p.board < i_o or p.alight > i_d)


def test_dates_shift_back_for_stations_reached_on_an_earlier_day():
    stops = build_route_stops(route_15708())
    gkp, asr, mfp = (_index(stops, c) for c in ("GKP", "ASR", "MFP"))
    # Boarding at GKP on day 2 (11th) means the train left ASR on day 1 (10th).
    assert booking_date("11-10-2026", stops, gkp, asr) == "10-10-2026"
    assert booking_date("11-10-2026", stops, gkp, gkp) == "11-10-2026"
    assert alighting_date("11-10-2026", stops, gkp, mfp) == "11-10-2026"
    dli = _index(stops, "DLI")
    assert alighting_date("10-10-2026", stops, dli, mfp) == "11-10-2026"  # overnight to MFP


def test_a_halt_that_crosses_midnight_departs_on_the_next_date():
    rows = [
        ("AAA", "A", 1, 0.0, None, "22:00", True),
        ("BBB", "B", 1, 100.0, "23:50", "00:10", True),  # arrives day 1, leaves day 2
        ("CCC", "C", 2, 200.0, "01:00", "01:05", True),
        ("DDD", "D", 2, 300.0, "02:00", None, True),
    ]
    stops = build_route_stops(make_schedule_route("1", "T", rows))
    assert booking_date("12-10-2026", stops, 1, 0) == "11-10-2026"  # AAA, a day earlier
    assert booking_date("12-10-2026", stops, 2, 1) == "12-10-2026"  # BBB leaves on day 2


def _suggestion(*, gain, fare, km):
    return SegmentSuggestion(
        booking_from="AAA",
        booking_to="BBB",
        travel_class="3A",
        fare=fare,
        extra_km=km,
        confirm_chance=70 + gain,
        baseline_confirm_chance=70,
        gain_pct=gain,
        instruction="x",
    )


def test_suggestion_sort_key_is_gain_then_fare_then_distance():
    items = [
        _suggestion(gain=10, fare=900, km=5),
        _suggestion(gain=15, fare=1000, km=9),
        _suggestion(gain=15, fare=800, km=9),
        _suggestion(gain=15, fare=800, km=3),
    ]
    ordered = sorted(items, key=suggestion_sort_key)
    assert [(x.gain_pct, x.fare, x.extra_km) for x in ordered] == [
        (15, 800, 3),
        (15, 800, 9),
        (15, 1000, 9),
        (10, 900, 5),
    ]


# --------------------------------------------------------------------------- #
# Service: the two motivating examples
# --------------------------------------------------------------------------- #
async def test_15708_dli_mfp_suggests_asr_mfp():
    svc, _ = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")

    assert report.reason is None and len(report.trains) == 1
    t = report.trains[0]
    assert (t.train_number, t.reason) == ("15708", None)

    by_pair = {(s.booking_from, s.booking_to): s for s in t.suggestions}
    s = by_pair[("ASR", "MFP")]
    assert (s.travel_class, s.quota) == ("3A", "GN")
    assert (s.status, s.confirm_chance, s.fare) == ("GNWL34/WL19", 87, 1735)
    assert (s.baseline_status, s.baseline_confirm_chance) == ("RLWL21/WL13", 72)
    assert (s.gain_pct, s.extra_fare, s.extra_km) == (15, 315, 447)
    assert (s.departure, s.arrival) == ("07:40", "14:55")
    assert (s.departure_date, s.arrival_date) == ("10-10-2026", "11-10-2026")
    assert s.instruction == "Book ASR->MFP, set boarding at DLI in IRCTC, alight at MFP"

    # Sorted by gain, then fare: ASR->KIR (+16), ASR->MFP (+15), SZM->MFP (+10, same fare).
    assert _pairs(t) == [("ASR", "KIR"), ("ASR", "MFP"), ("SZM", "MFP")]
    assert report.best is not None and report.best.train_number == "15708"
    assert report.best.suggestion == t.suggestions[0]

    leg = t.user_leg
    assert (leg.from_code, leg.to_code) == ("DLI", "MFP")
    assert (leg.departure, leg.arrival, leg.distance) == ("17:35", "14:55", 1077)
    assert (leg.departure_date, leg.arrival_date) == ("10-10-2026", "11-10-2026")
    assert (leg.travel_class, leg.fare, leg.status, leg.confirm_chance) == (
        "3A",
        1420,
        "RLWL21/WL13",
        72,
    )
    assert [c.travel_class for c in leg.classes] == ["3A"]

    # Proof: only the stations the result rests on, in route order, with real distances.
    assert [(p.code, p.distance_km, p.role) for p in t.route_proof] == [
        ("ASR", 0.0, "train_origin"),
        ("SZM", 444.0, "booking_from"),
        ("DLI", 447.0, "user_origin"),
        ("MFP", 1523.0, "user_destination"),
        ("KIR", 1805.0, "train_terminus"),
    ]
    assert (t.candidates_considered, t.candidates_evaluated, t.candidates_unavailable) == (48, 48, 41)


async def test_12204_dli_mfp_suggests_asr_shc_when_its_gain_clears_the_threshold():
    # Snapshot from the request: DLI->MFP RLWL 74%, ASR->SHC GNWL 79% Rs1305 (a 5-point gain).
    svc, _ = service_12204()
    report = await svc.find("DLI", "MFP", DATE, train_number="12204", min_gain_pct=5)

    t = report.trains[0]
    assert _pairs(t) == [("JUC", "MFP"), ("PGW", "MFP"), ("ASR", "MFP"), ("ASR", "SHC")]
    s = next(x for x in t.suggestions if (x.booking_from, x.booking_to) == ("ASR", "SHC"))
    assert (s.status, s.confirm_chance, s.fare) == ("GNWL116/WL54", 79, 1305)
    assert (s.baseline_status, s.baseline_confirm_chance) == ("RLWL99/WL64", 74)
    assert (s.gain_pct, s.extra_fare, s.extra_km) == (5, 290, 659)  # 447 km back + 212 km on
    assert (s.departure_date, s.arrival_date) == ("10-10-2026", "11-10-2026")
    assert s.instruction == "Book ASR->SHC, set boarding at DLI in IRCTC, alight at MFP"


async def test_12204_default_threshold_keeps_only_gains_of_ten_points():
    # Same data, default min_gain_pct=10: the 5- and 9-point gains are filtered out.
    svc, _ = service_12204()
    report = await svc.find("DLI", "MFP", DATE, train_number="12204")
    assert _pairs(report.trains[0]) == [("JUC", "MFP"), ("PGW", "MFP")]
    assert [s.gain_pct for s in report.trains[0].suggestions] == [12, 11]
    assert report.best.suggestion.instruction.startswith("Book JUC->MFP")


async def test_12204_asr_shc_leads_at_default_threshold_once_its_prediction_rises():
    # The same pair later predicted 90% (GNWL114/WL52): now a 16-point gain, best overall.
    svc, _ = service_12204(asr_shc=("GNWL114/WL52", 90, 1305))
    report = await svc.find("DLI", "MFP", DATE, train_number="12204")
    assert _pairs(report.trains[0])[0] == ("ASR", "SHC")
    assert report.best.suggestion.gain_pct == 16


async def test_a_tight_budget_stops_before_the_seventh_candidate():
    # Full-window default looks up BEAS; an explicit low budget still stops before it.
    svc, provider = service_12204()
    report = await svc.find("DLI", "MFP", DATE, train_number="12204", min_gain_pct=0)
    assert ("BEAS", "MFP", DATE) in provider.calls
    assert ("BEAS", "MFP") in _pairs(report.trains[0])
    provider.calls.clear()
    await svc.find("DLI", "MFP", DATE, train_number="12204", min_gain_pct=0, max_candidates=6)
    assert ("BEAS", "MFP", DATE) not in provider.calls


# --------------------------------------------------------------------------- #
# Service: direction, halts, presence
# --------------------------------------------------------------------------- #
async def test_wrong_direction_returns_empty_with_reason_and_no_availability_lookups():
    svc, provider = service_15708()
    report = await svc.find("MFP", "DLI", DATE, train_number="15708")

    t = report.trains[0]
    assert (
        t.suggestions == [] and t.reason == REASON_NOT_ON_ROUTE == "not on route / wrong direction"
    )
    assert "does not run MFP->DLI" in t.detail
    assert report.best is None
    assert report.reason == "No suggestions for train 15708: not on route / wrong direction."
    assert provider.calls == []  # never priced a reverse (or any) booking


async def test_non_halt_origin_returns_reason():
    svc, provider = service_15708()
    report = await svc.find("GZB", "MFP", DATE, train_number="15708")  # the train runs through GZB

    t = report.trains[0]
    assert t.suggestions == [] and t.reason == REASON_NOT_A_HALT
    assert "GZB" in t.detail and "origin" in t.detail
    assert provider.calls == []


async def test_non_halt_destination_returns_reason():
    svc, _ = service_15708()
    report = await svc.find("DLI", "GZB", DATE, train_number="15708")
    assert report.trains[0].reason == REASON_NOT_A_HALT
    assert "destination" in report.trains[0].detail


async def test_station_absent_from_the_route_is_not_on_route():
    svc, _ = service_15708()
    report = await svc.find("DLI", "NDLS", DATE, train_number="15708")
    t = report.trains[0]
    assert t.reason == REASON_NOT_ON_ROUTE and "NDLS" in t.detail and t.suggestions == []


async def test_halt_minutes_missing_upstream_still_count_as_a_halt_when_times_differ():
    # Upstream drops HaltMinutes on some real halts; DLI/MFP still halt 15/10 min by the clock.
    provider = make_provider([("15708", "ASR KIR EXPRESS", PAIRS_15708)])
    routes = {"15708": route_15708(omit_halt=frozenset({"DLI", "MFP"}))}
    report = await make_service(provider, routes).find(
        "DLI", "MFP", DATE, train_number="15708", travel_class="3A"
    )
    t = report.trains[0]
    assert t.reason is None and ("ASR", "MFP") in _pairs(t)
    assert "DLI halts 15 min" in " ".join(t.warnings)


async def test_suggestions_never_run_against_the_trains_direction():
    for svc, number, dest in (
        (service_15708()[0], "15708", "MFP"),
        (service_12204()[0], "12204", "MFP"),
    ):
        report = await svc.find(
            "DLI",
            dest,
            DATE,
            train_number=number,
            min_gain_pct=0,
            max_candidates=24,
            max_extra_stations_each_side=15,
        )
        route = route_15708() if number == "15708" else route_12204()
        order = {s.code: i for i, s in enumerate(route.stops)}
        t = report.trains[0]
        assert t.suggestions, number
        for s in t.suggestions:
            assert order[s.booking_from] <= order["DLI"] and order[s.booking_to] >= order[dest]
            assert (s.booking_from, s.booking_to) != ("DLI", dest)
            assert order[s.booking_from] < order[s.booking_to]


# --------------------------------------------------------------------------- #
# Service: dates, classes, budget
# --------------------------------------------------------------------------- #
async def test_overnight_booking_is_searched_and_reported_on_its_own_earlier_date():
    pairs = {
        # The user boards at Gorakhpur on the 11th; the train left Amritsar on the 10th.
        ("GKP", "MFP"): dict(
            dep="08:20",
            arr="14:55",
            km=293,
            date="11-10-2026",
            avail=[("3A", "RLWL12/WL8", 60, 760)],
        ),
        ("ASR", "MFP"): PAIRS_15708[("ASR", "MFP")],  # keyed on 10-10-2026
    }
    provider = make_provider([("15708", "ASR KIR EXPRESS", pairs)])
    svc = make_service(provider, {"15708": route_15708()})
    report = await svc.find("GKP", "MFP", "11-10-2026", train_number="15708", travel_class="3A")

    t = report.trains[0]
    assert t.user_leg.departure_date == "11-10-2026"
    assert _pairs(t) == [("ASR", "MFP")]
    s = t.suggestions[0]
    assert (s.departure_date, s.arrival_date) == ("10-10-2026", "11-10-2026")
    assert ("ASR", "MFP", "10-10-2026") in provider.calls
    assert ("ASR", "MFP", "11-10-2026") not in provider.calls
    assert any(w.startswith("Date shift") and "ASR->MFP on 10-10-2026" in w for w in t.warnings)


async def test_without_a_class_every_class_is_compared_against_its_own_baseline():
    svc, _ = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708")
    t = report.trains[0]

    leg = t.user_leg
    assert [c.travel_class for c in leg.classes] == ["SL", "3E", "3A", "2A"]
    assert leg.travel_class is None and leg.fare is None  # no arbitrary "the" class

    rows = [(s.travel_class, s.booking_from, s.booking_to, s.gain_pct) for s in t.suggestions]
    assert rows == [
        ("3E", "ASR", "MFP", 31),
        ("3E", "ASR", "KIR", 30),
        ("2A", "ASR", "MFP", 19),
        ("3A", "ASR", "KIR", 16),
        ("3A", "ASR", "MFP", 15),
        ("3A", "SZM", "MFP", 10),
    ]
    sl = next(c for c in leg.classes if c.travel_class == "SL")
    assert sl.confirm_chance == 72  # SL ASR->MFP is 78%: +6, below the threshold, so absent


async def test_a_requested_class_limits_the_comparison_to_that_class():
    svc, _ = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="2a")
    t = report.trains[0]
    assert report.travel_class == "2A"
    assert _pairs(t) == [("ASR", "MFP")] and t.suggestions[0].travel_class == "2A"
    assert t.suggestions[0].gain_pct == 19


async def test_max_candidates_caps_upstream_lookups_nearest_first():
    svc, provider = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", max_candidates=2)

    t = report.trains[0]
    assert (t.candidates_considered, t.candidates_evaluated) == (48, 2)
    assert provider.calls[0] == ("DLI", "MFP", DATE)  # the baseline
    assert sorted(provider.calls[1:]) == [("ASR", "KIR", DATE), ("ASR", "MFP", DATE)]
    assert len(provider.calls) == 3


async def test_default_budget_evaluates_the_full_station_window():
    svc, provider = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    t = report.trains[0]
    looked_up = {c[:2] for c in provider.calls}
    assert t.candidates_evaluated == t.candidates_considered == 48
    assert ("PNP", "MFP") in looked_up and ("KUN", "MFP") in looked_up
    assert len(provider.calls) == 1 + 48


async def test_unavailable_candidates_are_counted_and_called_out():
    svc, _ = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    t = report.trains[0]
    assert t.candidates_unavailable == 41  # most window pairs are not sold in 3A
    assert any("41 candidate segment(s) returned no usable availability" in w for w in t.warnings)


async def test_search_cache_is_reused_across_calls():
    svc, provider = service_15708(cached=True)
    first = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    looked_up = len(provider.calls)
    second = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    assert looked_up == 49 and len(provider.calls) == looked_up  # nothing re-fetched
    assert second.trains[0].suggestions == first.trains[0].suggestions


async def test_the_confirmation_estimator_is_applied_to_candidates():
    class Skeptic:
        """Discounts every general-waitlist prediction by 10 points."""

        def estimate(self, availability):
            if (availability.status or "").startswith("GNWL") and availability.confirm_chance:
                return availability.model_copy(
                    update={"confirm_chance": availability.confirm_chance - 10}
                )
            return availability

    svc, _ = service_15708(estimator=Skeptic())
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    # ASR->MFP 87 -> 77 and ASR->KIR 88 -> 78 no longer clear +10; RLWL SZM->MFP (+10) does.
    assert _pairs(report.trains[0]) == [("SZM", "MFP")]


# --------------------------------------------------------------------------- #
# Service: nothing to suggest
# --------------------------------------------------------------------------- #
async def test_no_gain_reports_the_best_improvement_seen():
    svc, _ = service_12204()
    report = await svc.find("DLI", "MFP", DATE, train_number="12204", min_gain_pct=20)
    t = report.trains[0]
    assert t.suggestions == [] and t.reason == REASON_NO_GAIN
    assert "+12 points (JUC->MFP 3A)" in t.detail and "required +20" in t.detail
    assert t.user_leg.confirm_chance == 74  # the baseline is still reported
    assert report.best is None
    assert report.reason == f"No suggestions for train 12204: {REASON_NO_GAIN}."


async def test_a_baseline_that_cannot_gain_enough_skips_candidate_lookups():
    pairs = {
        ("DLI", "MFP"): dict(
            dep="17:35", arr="14:55", km=1077, avail=[("3A", "AVAILABLE-0122", 100, 1420)]
        )
    }
    provider = make_provider([("15708", "ASR KIR EXPRESS", pairs)])
    svc = make_service(provider, {"15708": route_15708()})
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")

    t = report.trains[0]
    assert t.reason == REASON_BASELINE_STRONG and t.candidates_evaluated == 0
    assert provider.calls == [("DLI", "MFP", DATE)]  # only the baseline was fetched


async def test_a_neighbouring_station_variant_is_not_mistaken_for_the_requested_pair():
    pairs = {
        ("DLI", "MFP"): PAIRS_15708[("DLI", "MFP")],
        # Upstream answers an ASR->MFP search with the train *from another station*: that is a
        # different booking, so it must not be priced as ASR->MFP.
        ("ASR", "MFP"): {**PAIRS_15708[("ASR", "MFP")], "from": "LDH"},
    }
    provider = make_provider([("15708", "ASR KIR EXPRESS", pairs)])
    svc = make_service(provider, {"15708": route_15708()})
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    t = report.trains[0]
    assert t.suggestions == [] and t.candidates_unavailable == 48  # none of the window priced
    assert t.reason == REASON_NO_GAIN and "none of them returned usable availability" in t.detail


async def test_a_cancelled_or_closed_candidate_is_never_suggested():
    pairs = {
        ("DLI", "MFP"): PAIRS_15708[("DLI", "MFP")],
        ("ASR", "MFP"): {
            "dep": "07:40",
            "arr": "14:55",
            "km": 1522,
            "avail": [("3A", "TRAIN CANCELLED", 95, 1735)],  # a stale prediction on a dead row
        },
    }
    provider = make_provider([("15708", "ASR KIR EXPRESS", pairs)])
    svc = make_service(provider, {"15708": route_15708()})
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    assert report.trains[0].suggestions == []


async def test_user_leg_spanning_the_whole_run_has_nothing_longer_to_book():
    svc, provider = service_15708()
    report = await svc.find("ASR", "KIR", DATE, train_number="15708")
    assert report.trains[0].reason == REASON_NO_EXTENSION
    assert provider.calls == []


async def test_train_not_offered_on_the_user_leg_reports_no_baseline():
    provider = make_provider([("15708", "ASR KIR EXPRESS", {})])
    svc = make_service(provider, {"15708": route_15708()})
    report = await svc.find("DLI", "MFP", DATE, train_number="15708")
    t = report.trains[0]
    assert t.reason == REASON_NO_BASELINE and "no availability between DLI and MFP" in t.detail


async def test_requested_class_not_sold_on_the_user_leg():
    svc, _ = service_15708()
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="1A")
    t = report.trains[0]
    assert t.reason == REASON_CLASS_NOT_OFFERED and "offered: SL, 3E, 3A, 2A" in t.detail


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quota": "TQ"},
        {"max_candidates": 0},
        {"max_candidates": 200},
        {"max_extra_stations_each_side": -1},
        {"min_gain_pct": 101},
        {"min_gain_pct": -1},
    ],
)
async def test_invalid_parameters_are_rejected(kwargs):
    svc, provider = service_15708()
    with pytest.raises(InvalidInputError):
        await svc.find("DLI", "MFP", DATE, train_number="15708", **kwargs)
    assert provider.calls == []


async def test_identical_origin_and_destination_are_rejected():
    svc, _ = service_15708()
    with pytest.raises(InvalidInputError):
        await svc.find("dli", "DLI", DATE, train_number="15708")


async def test_general_quota_aliases_are_accepted():
    svc, _ = service_15708()
    for quota in (None, "", "gn", "General"):
        report = await svc.find("DLI", "MFP", DATE, train_number="15708", quota=quota)
        assert report.quota == "GN"


async def test_only_general_quota_entries_are_compared():
    pairs = {
        ("DLI", "MFP"): dict(
            dep="17:35", arr="14:55", km=1077, avail=[("3A", "RLWL21/WL13", 72, 1420)]
        ),
        ("ASR", "MFP"): dict(
            dep="07:40", arr="14:55", km=1522, avail=[("3A", "GNWL34/WL19", 87, 1735)]
        ),
    }
    provider = make_provider([("15708", "ASR KIR EXPRESS", pairs)])
    # A Tatkal row for the same class must never be mistaken for the general one.
    provider.results[("ASR", "MFP", DATE)].trains[0].availability.insert(
        0, make_avail("3A", fare=2200, chance=99, status="TQ AVL 5", quota="TQ")
    )
    svc = make_service(provider, {"15708": route_15708()})
    report = await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")
    s = next(x for x in report.trains[0].suggestions if x.booking_from == "ASR")
    assert (s.quota, s.fare, s.confirm_chance) == ("GN", 1735, 87)


# --------------------------------------------------------------------------- #
# Service: warnings
# --------------------------------------------------------------------------- #
async def test_warnings_cover_boarding_change_fare_early_alight_and_halt():
    svc, _ = service_15708()
    t = (await svc.find("DLI", "MFP", DATE, train_number="15708", travel_class="3A")).trains[0]
    joined = "\n".join(t.warnings)
    assert "Boarding change required" in joined and "change the boarding point to DLI" in joined
    assert "confirmed/RAC" in joined and "not while waitlisted" in joined
    assert "Pay extra, no refund for the unused leg" in joined
    assert "before DLI and after MFP" in joined
    assert "Early alight forfeits the balance" in joined and "get off at MFP" in joined
    assert "Verify halt: DLI halts 15 min; MFP halts 10 min" in joined


async def test_boarding_only_suggestions_do_not_warn_about_early_alight():
    svc, _ = service_12204()
    t = (await svc.find("DLI", "MFP", DATE, train_number="12204")).trains[0]
    assert _pairs(t) == [("JUC", "MFP"), ("PGW", "MFP")]  # the destination is unchanged
    joined = "\n".join(t.warnings)
    assert "Boarding change required" in joined
    assert "Early alight" not in joined and "after MFP" not in joined
    assert "the stretch before DLI is paid for" in joined


# --------------------------------------------------------------------------- #
# Service: corridor search (no train_number)
# --------------------------------------------------------------------------- #
def _corridor_service(**kw):
    provider = make_provider(
        [("15708", "ASR KIR EXPRESS", PAIRS_15708), ("12204", "SHC GARIB RATH", pairs_12204())]
    )
    routes = {"15708": route_15708(), "12204": route_12204()}
    return make_service(provider, routes, **kw), provider


async def test_corridor_search_analyses_every_direct_train_best_first():
    svc, _ = _corridor_service()
    report = await svc.find("DLI", "MFP", DATE, travel_class="3A")

    assert (report.origin, report.destination, report.train_number) == ("DLI", "MFP", None)
    assert report.trains_matched == 2
    assert [t.train_number for t in report.trains] == ["15708", "12204"]  # +16 beats +12
    assert report.best.train_number == "15708"
    assert [t.train_name for t in report.trains] == ["ASR KIR EXPRESS", "SHC GARIB RATH"]


async def test_corridor_search_lists_trains_that_cannot_be_analysed_with_their_reason():
    provider = make_provider(
        [("15708", "ASR KIR EXPRESS", PAIRS_15708), ("12204", "SHC GARIB RATH", pairs_12204())]
    )
    svc = make_service(provider, {"15708": route_15708()})  # no route for 12204
    report = await svc.find("DLI", "MFP", DATE, travel_class="3A")

    good, broken = report.trains
    assert good.train_number == "15708" and good.suggestions
    assert broken.train_number == "12204" and broken.reason == REASON_NO_ROUTE
    assert "no route for 12204" in broken.detail
    assert report.best.train_number == "15708"


async def test_corridor_search_isolates_an_unexpected_failure_to_its_train():
    class Boom:
        def estimate(self, availability):
            if (availability.status or "").startswith("PQWL"):
                raise RuntimeError("estimator exploded")
            return availability

    svc, _ = _corridor_service(estimator=Boom())
    report = await svc.find("DLI", "MFP", DATE, travel_class="3A")
    by = {t.train_number: t for t in report.trains}
    assert by["12204"].reason == REASON_INTERNAL and "RuntimeError" in by["12204"].detail
    assert by["15708"].suggestions and by["15708"].reason is None


def _variant_setup():
    """15708 (halts at DLI) plus 12566, which the corridor search returns from NDLS."""
    route_12566 = make_schedule_route(
        "12566",
        "Bihar S Kranti",
        [
            ("NDLS", "New Delhi", 1, 0.0, None, "12:45", True),
            ("CNB", "Kanpur Central", 1, 440.0, "18:10", "18:15", True),
            ("MFP", "Muzaffarpur Jn", 2, 1090.0, "07:00", "07:10", True),
            ("SPJ", "Samastipur Jn", 2, 1140.0, "08:05", None, True),
        ],
    )
    variant = {
        # Listed under the requested DLI->MFP corridor, but boarding at the neighbouring NDLS.
        ("DLI", "MFP"): {
            "from": "NDLS",
            "dep": "12:45",
            "arr": "07:00",
            "km": 1090,
            "avail": [("3E", "GNWL41/WL28", 29, 1365)],
        },
        ("NDLS", "SPJ"): {
            "dep": "12:45",
            "arr": "08:05",
            "km": 1140,
            "avail": [("3E", "GNWL31/WL25", 62, 1405)],
        },
    }
    return route_12566, variant


async def test_corridor_trains_from_a_neighbouring_station_are_analysed_from_there():
    route_12566, variant = _variant_setup()
    provider = make_provider(
        [("12566", "BIHAR S KRANTI", variant), ("15708", "ASR KIR EXPRESS", PAIRS_15708)]
    )
    svc = make_service(provider, {"15708": route_15708(), "12566": route_12566})
    report = await svc.find("DLI", "MFP", DATE, travel_class="3E")

    t = next(x for x in report.trains if x.train_number == "12566")
    assert t.reason is None and (t.user_leg.from_code, t.user_leg.to_code) == ("NDLS", "MFP")
    assert any("nearby NDLS, not DLI" in w for w in t.warnings)
    assert _pairs(t) == [("NDLS", "SPJ")]
    assert t.suggestions[0].instruction == (
        "Book NDLS->SPJ, set boarding at NDLS in IRCTC, alight at MFP"
    )
    assert t.suggestions[0].gain_pct == 33
    # The seeded corridor row was the baseline: no extra lookup for NDLS->MFP.
    assert ("NDLS", "MFP", DATE) not in provider.calls


async def test_exact_corridor_trains_take_the_limited_slots_before_neighbouring_variants():
    route_12566, variant = _variant_setup()
    provider = make_provider(  # the variant comes first in the upstream order
        [("12566", "BIHAR S KRANTI", variant), ("15708", "ASR KIR EXPRESS", PAIRS_15708)]
    )
    svc = make_service(provider, {"15708": route_15708(), "12566": route_12566})
    report = await svc.find("DLI", "MFP", DATE, travel_class="3E", train_limit=1)
    assert report.trains_matched == 2
    assert [t.train_number for t in report.trains] == ["15708"]


async def test_corridor_search_without_trains_says_so():
    provider = make_provider([])
    svc = make_service(provider, {})
    report = await svc.find("DLI", "MFP", DATE)
    assert report.trains == [] and report.best is None and report.trains_matched == 0
    assert report.reason == "No direct trains found between DLI and MFP on 10-10-2026."


async def test_corridor_search_with_a_class_skips_trains_that_do_not_sell_it():
    svc, _ = _corridor_service()
    report = await svc.find("DLI", "MFP", DATE, travel_class="2A")
    assert [t.train_number for t in report.trains] == ["15708"]  # 12204 is 3A only
    assert report.trains_matched == 1
