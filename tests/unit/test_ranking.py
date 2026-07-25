"""Unit tests for pure ranking/filtering logic."""

from __future__ import annotations

from transit_mcp.models.enums import SortBy
from transit_mcp.services.ranking import (
    TrainFilters,
    apply_preferred,
    best_confirm_chance,
    class_is_available,
    filter_trains,
    min_fare,
    sort_trains,
)

from tests.fakes import make_avail, make_train


def _sample():
    return [
        make_train(
            "111",
            departure="22:00",
            arrival="06:00",
            duration_min=480,
            distance=600,
            availability=[make_avail("SL", fare=500, chance=90), make_avail("3A", fare=1200, chance=40)],
        ),
        make_train(
            "222",
            departure="06:00",
            arrival="20:00",
            duration_min=840,
            distance=900,
            availability=[make_avail("SL", fare=300, chance=20)],
        ),
        make_train(
            "333",
            departure="14:00",
            arrival="16:00",
            duration_min=120,
            distance=150,
            availability=[make_avail("2A", fare=2000, chance=99, status="Confirm")],
        ),
    ]


def test_sort_by_departure_duration_price_confirmation():
    trains = _sample()
    assert [t.number for t in sort_trains(trains, SortBy.DEPARTURE)] == ["222", "333", "111"]
    assert [t.number for t in sort_trains(trains, SortBy.DURATION)] == ["333", "111", "222"]
    assert [t.number for t in sort_trains(trains, SortBy.PRICE)] == ["222", "111", "333"]
    assert [t.number for t in sort_trains(trains, SortBy.CONFIRMATION)] == ["333", "111", "222"]
    assert [t.number for t in sort_trains(trains, SortBy.DISTANCE)] == ["333", "111", "222"]


def test_sort_default_preserves_order():
    trains = _sample()
    assert [t.number for t in sort_trains(trains, SortBy.DEFAULT)] == ["111", "222", "333"]


def test_filter_by_class_and_fare_and_chance():
    trains = _sample()
    only_3a = filter_trains(trains, TrainFilters(classes=frozenset({"3A"})))
    assert [t.number for t in only_3a] == ["111"]

    cheap = filter_trains(trains, TrainFilters(max_fare=400))
    assert [t.number for t in cheap] == ["222"]

    confident = filter_trains(trains, TrainFilters(min_confirm_chance=80))
    assert {t.number for t in confident} == {"111", "333"}


def test_filter_by_departure_window():
    trains = _sample()
    morning = filter_trains(trains, TrainFilters(depart_after="05:00", depart_before="15:00"))
    assert {t.number for t in morning} == {"222", "333"}


def test_available_only_excludes_regret():
    t = make_train("999", availability=[make_avail("SL", chance=None, seats=None, status="Regret")])
    assert filter_trains([t], TrainFilters(available_only=True)) == []
    assert not class_is_available(t.availability[0])


def test_helpers_min_fare_and_best_chance():
    t = _sample()[0]
    assert min_fare(t) == 500
    assert min_fare(t, classes={"3A"}) == 1200
    assert best_confirm_chance(t) == 90


def test_apply_preferred_moves_to_front():
    trains = _sample()
    ordered = apply_preferred(trains, "333")
    assert ordered[0].number == "333"
    assert {t.number for t in ordered} == {"111", "222", "333"}
