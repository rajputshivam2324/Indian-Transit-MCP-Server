"""Unit tests for services wired to in-memory fake providers (no network)."""

from __future__ import annotations

import pytest

from transit_mcp.infra.errors import NoResultsError, StationNotFoundError
from transit_mcp.models.domain import Station, TrainSearchResult
from transit_mcp.models.enums import SortBy
from transit_mcp.providers.estimators import PassthroughConfirmationEstimator
from transit_mcp.services.ranking import TrainFilters
from transit_mcp.services.stations import StationService
from transit_mcp.services.trains import (
    AvailabilityService,
    ConfirmationService,
    TrainSearchService,
)

from tests.fakes import (
    FakeStationProvider,
    FakeTrainProvider,
    make_avail,
    make_train,
)

DATE = "24-07-2026"


def _station_service() -> StationService:
    mapping = {
        "delhi": [
            Station(code="DLI", name="Old Delhi", city="Delhi", state="Delhi", is_major=False),
            Station(code="NDLS", name="New Delhi", city="Delhi", state="Delhi", is_major=True),
        ],
        "mumbai": [
            Station(code="MMCT", name="Mumbai Central", city="Mumbai", state="MH", is_major=True),
        ],
    }
    return StationService(FakeStationProvider(mapping))


async def test_station_resolve_prefers_major_and_returns_suggestions():
    svc = _station_service()
    best, suggestions = await svc.resolve("delhi")
    assert best.code == "NDLS"  # major beats non-major at equal name match
    assert {s.code for s in suggestions} == {"NDLS", "DLI"}


async def test_station_resolve_exact_code_wins():
    svc = _station_service()
    provider = FakeStationProvider(
        {"ndls": [
            Station(code="NDLSX", name="Decoy", is_major=True),
            Station(code="NDLS", name="New Delhi", is_major=False),
        ]}
    )
    svc = StationService(provider)
    assert await svc.resolve_code("NDLS") == "NDLS"


async def test_station_resolve_empty_raises():
    svc = _station_service()
    with pytest.raises(StationNotFoundError):
        await svc.resolve("   ")


def _train_search_service() -> tuple[TrainSearchService, FakeStationProvider]:
    trains = [
        make_train("111", departure="22:00", duration_min=480, availability=[make_avail("SL", fare=500, chance=90)]),
        make_train("222", departure="06:00", duration_min=840, availability=[make_avail("SL", fare=300, chance=20)]),
        make_train("333", departure="14:00", duration_min=120, availability=[make_avail("2A", fare=2000, chance=99)]),
    ]
    result = TrainSearchResult(
        source_code="NDLS", destination_code="MMCT", date=DATE,
        source_name="New Delhi", destination_name="Mumbai Central",
        quotas=["GN", "TQ"], trains=trains,
    )
    train_provider = FakeTrainProvider({("NDLS", "MMCT", DATE): result})
    station_provider = FakeStationProvider()  # echoes codes
    svc = TrainSearchService(train_provider, StationService(station_provider), default_limit=20)
    return svc, station_provider


async def test_search_resolves_both_endpoints_concurrently():
    svc, station_provider = _train_search_service()
    resp = await svc.search("NDLS", "MMCT", DATE)
    assert resp.origin == "NDLS" and resp.destination == "MMCT"
    assert resp.source_name == "New Delhi"
    assert resp.total_matched == 3
    assert set(station_provider.calls) == {"NDLS", "MMCT"}


async def test_search_applies_filter_sort_limit():
    svc, _ = _train_search_service()
    resp = await svc.search(
        "NDLS", "MMCT", DATE,
        filters=TrainFilters(min_confirm_chance=80),
        sort_by=SortBy.DURATION,
        limit=1,
    )
    assert resp.total_matched == 2  # trains 111 and 333 pass the chance filter
    assert len(resp.trains) == 1  # limited
    assert resp.trains[0].number == "333"  # shortest duration first


async def test_availability_and_confirmation_services():
    svc, _ = _train_search_service()
    avail_svc = AvailabilityService(svc)
    train = await avail_svc.get_train("333", "NDLS", "MMCT", DATE)
    assert train.number == "333"

    conf_svc = ConfirmationService(avail_svc, PassthroughConfirmationEstimator())
    pred = await conf_svc.predict("333", "NDLS", "MMCT", DATE, "2A")
    assert pred.confirm_chance == 99
    assert pred.travel_class == "2A"

    with pytest.raises(NoResultsError):
        await conf_svc.predict("333", "NDLS", "MMCT", DATE, "SL")  # class not on train
