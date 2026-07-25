"""Mapper tests against real captured ConfirmTkt payloads."""

from __future__ import annotations

from transit_mcp.providers.confirmtkt.mappers import (
    map_route,
    map_search_result,
    map_stations,
)


def test_map_stations_parses_station_list(autosuggest_payload):
    stations = map_stations(autosuggest_payload)
    assert stations, "expected at least one station"
    ndls = next(s for s in stations if s.code == "NDLS")
    assert ndls.city == "New Delhi"
    assert ndls.state == "Delhi"
    assert ndls.airport_code == "DEL"
    assert ndls.is_major is True
    assert ndls.latitude and 28 < ndls.latitude < 29


def test_map_search_result_basic(search_payload):
    result = map_search_result(search_payload, src="NDLS", dst="MMCT", date="24-07-2026")
    assert result.source_code == "NDLS"
    assert result.destination_code == "MMCT"
    assert result.date == "24-07-2026"
    assert result.quotas  # e.g. ['SS','GN','LD','TQ']
    assert len(result.trains) >= 1


def test_map_train_availability_and_computed(search_payload):
    result = map_search_result(search_payload, src="NDLS", dst="MMCT", date="24-07-2026")
    train = result.trains[0]
    assert train.number
    assert train.duration_min > 0
    assert "h" in train.duration_fmt
    assert train.availability, "expected class availability entries"
    a = train.availability[0]
    assert a.travel_class
    # fare and confirmation come through as parsed values when present
    assert any(x.fare is not None for x in train.availability)
    assert any(x.confirm_chance is not None for x in train.availability)


def test_map_route_orders_major_and_intermediate(schedule_payload):
    route = map_route(schedule_payload)
    assert route.number == "12951"
    assert route.from_code == "MMCT"
    assert route.to_code == "NDLS"
    assert route.total_duration_min and route.total_duration_min > 0
    codes = [s.code for s in route.stops]
    # Origin first, destination last, no consecutive duplicates.
    assert codes[0] == "MMCT"
    assert codes[-1] == "NDLS"
    assert all(codes[i] != codes[i + 1] for i in range(len(codes) - 1))
    # Major stations present and flagged.
    majors = [s.code for s in route.stops if s.is_major]
    assert {"MMCT", "BVI", "ST", "BRC", "RTM", "KOTA", "NDLS"}.issubset(set(majors))
    # Distances are non-decreasing along the route.
    dists = [s.distance_from_origin for s in route.stops if s.distance_from_origin is not None]
    assert dists == sorted(dists)
