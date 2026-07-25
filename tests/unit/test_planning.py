"""Unit tests for split-journey planning and multi-modal comparison."""

from __future__ import annotations

from transit_mcp.common import add_days
from transit_mcp.models.domain import BusTrip, TrainSearchResult
from transit_mcp.models.enums import TransportMode
from transit_mcp.services.planning import MultiModalService, SplitJourneyService
from transit_mcp.services.routes import RouteService
from transit_mcp.services.stations import StationService
from transit_mcp.services.trains import TrainSearchService

from tests.fakes import (
    FakeBusProvider,
    FakeRouteProvider,
    FakeStationProvider,
    FakeTrainProvider,
    make_avail,
    make_route,
    make_train,
)

DATE = "24-07-2026"
NEXT = add_days(DATE, 1)


def _split_service():
    baseline = TrainSearchResult(
        source_code="AAA", destination_code="CCC", date=DATE,
        trains=[make_train("900", from_code="AAA", to_code="CCC", distance=1000)],
    )
    leg1 = TrainSearchResult(
        source_code="AAA", destination_code="BBB", date=DATE,
        trains=[make_train(
            "L1", from_code="AAA", to_code="BBB",
            departure="08:00", duration_min=240,  # arrives BBB 12:00
            availability=[make_avail("SL", fare=400, chance=80)],
        )],
    )
    leg2 = TrainSearchResult(
        source_code="BBB", destination_code="CCC", date=DATE,
        trains=[make_train(
            "L2", from_code="BBB", to_code="CCC",
            departure="13:00", duration_min=300,  # 60-min layover after L1
            availability=[make_avail("SL", fare=600, chance=50)],
        )],
    )
    train_provider = FakeTrainProvider({
        ("AAA", "CCC", DATE): baseline,
        ("AAA", "BBB", DATE): leg1,
        ("BBB", "CCC", DATE): leg2,
    })
    route_provider = FakeRouteProvider({
        "900": make_route("900", [
            ("AAA", 1, 0.0, True),
            ("BBB", 1, 400.0, True),
            ("CCC", 1, 1000.0, True),
        ]),
    })
    stations = StationService(FakeStationProvider())
    search = TrainSearchService(train_provider, stations)
    routes = RouteService(route_provider)
    return SplitJourneyService(stations, search, routes)


async def test_split_journey_finds_single_transfer_via_hub():
    svc = _split_service()
    itineraries = await svc.plan("AAA", "CCC", DATE)
    assert len(itineraries) == 1
    it = itineraries[0]
    assert it.transfer_station == "BBB"
    assert [leg.train.number for leg in it.legs] == ["L1", "L2"]
    assert it.layover_min == 60
    assert it.total_duration_min == 600  # 08:00 -> 18:00
    assert it.total_fare == 1000
    assert it.combined_confirm_chance == 40.0  # 0.8 * 0.5
    assert it.requires_station_change is False
    assert it.transfer_arrival_station == "BBB"
    assert it.transfer_departure_station == "BBB"


async def test_split_journey_respects_min_transfer():
    svc = _split_service()
    # Require an impossible 3h minimum transfer -> no itineraries.
    itineraries = await svc.plan("AAA", "CCC", DATE, min_transfer_min=180)
    assert itineraries == []


async def test_split_journey_derives_hubs_from_variant_endpoints():
    """Regression: 'nearby' search returns trains whose endpoints are variants of
    the requested stations, so hub derivation must bound by the train's own ends."""
    baseline = TrainSearchResult(
        source_code="AAA", destination_code="CCC", date=DATE,
        # Train runs NZM->BVI (variants of requested AAA/CCC), not AAA/CCC directly.
        trains=[make_train("900", from_code="NZM", to_code="BVI", distance=1000)],
    )
    leg1 = TrainSearchResult(
        source_code="AAA", destination_code="BBB", date=DATE,
        trains=[make_train(
            "L1", from_code="AAA", to_code="BBB",
            departure="08:00", duration_min=240,
            availability=[make_avail("SL", fare=400, chance=80)],
        )],
    )
    leg2 = TrainSearchResult(
        source_code="BBB", destination_code="CCC", date=DATE,
        trains=[make_train(
            "L2", from_code="BBB", to_code="CCC",
            departure="13:00", duration_min=300,
            availability=[make_avail("SL", fare=600, chance=50)],
        )],
    )
    train_provider = FakeTrainProvider({
        ("AAA", "CCC", DATE): baseline,
        ("AAA", "BBB", DATE): leg1,
        ("BBB", "CCC", DATE): leg2,
    })
    # Route uses the train's own (variant) endpoints, with BBB as the major hub.
    route_provider = FakeRouteProvider({
        "900": make_route("900", [
            ("NZM", 1, 0.0, True),
            ("BBB", 1, 400.0, True),
            ("BVI", 1, 1000.0, True),
        ]),
    })
    stations = StationService(FakeStationProvider())
    search = TrainSearchService(train_provider, stations)
    svc = SplitJourneyService(stations, search, RouteService(route_provider))

    itineraries = await svc.plan("AAA", "CCC", DATE)
    assert len(itineraries) == 1
    assert itineraries[0].transfer_station == "BBB"


async def test_split_journey_fare_matches_shown_class_not_cheapest():
    """Regression: with no class filter, the itinerary must pick a class available on
    both legs and price THAT class on both legs (not the cheapest class per leg).
    Mirrors the real MFP->NDLS case (leg 1 SL 100% cheap, but 3A gives the best
    weakest-leg confirmation across both legs)."""
    baseline = TrainSearchResult(
        source_code="AAA", destination_code="CCC", date=DATE,
        trains=[make_train("900", from_code="AAA", to_code="CCC", distance=1000)],
    )
    leg1 = TrainSearchResult(
        source_code="AAA", destination_code="BBB", date=DATE,
        trains=[make_train(
            "02569", from_code="AAA", to_code="BBB",
            departure="09:10", duration_min=188,  # arrives 12:18
            availability=[
                make_avail("SL", fare=500, chance=100),
                make_avail("3A", fare=1120, chance=100),
            ],
        )],
    )
    leg2 = TrainSearchResult(
        source_code="BBB", destination_code="CCC", date=DATE,
        trains=[make_train(
            "02563", from_code="BBB", to_code="CCC",
            departure="13:12", duration_min=960,  # 54-min layover
            availability=[
                make_avail("SL", fare=600, chance=40),   # weak on the 2nd leg
                make_avail("3A", fare=1625, chance=93),
            ],
        )],
    )
    train_provider = FakeTrainProvider({
        ("AAA", "CCC", DATE): baseline,
        ("AAA", "BBB", DATE): leg1,
        ("BBB", "CCC", DATE): leg2,
    })
    route_provider = FakeRouteProvider({
        "900": make_route("900", [
            ("AAA", 1, 0.0, True),
            ("BBB", 1, 400.0, True),
            ("CCC", 1, 1000.0, True),
        ]),
    })
    stations = StationService(FakeStationProvider())
    search = TrainSearchService(train_provider, stations)
    svc = SplitJourneyService(stations, search, RouteService(route_provider))

    itineraries = await svc.plan("AAA", "CCC", DATE)
    assert len(itineraries) == 1
    it = itineraries[0]
    # 3A maximizes the weakest leg (min(100, 93)) so it beats SL (min(100, 40)).
    assert it.travel_class == "3A"
    assert it.legs[0].availability.travel_class == "3A"
    assert it.legs[1].availability.travel_class == "3A"
    # total_fare is the sum of the SHOWN class on both legs, not the cheapest class.
    assert it.total_fare == 1120 + 1625 == 2745
    assert it.combined_confirm_chance == 93.0  # 100% * 93%
    assert it.layover_min == 54


async def test_split_journey_flags_same_city_station_change():
    """Leg 1 arrives BPL but leg 2 departs RKMP (same city, different station):
    the itinerary must expose both and flag requires_station_change with a warning."""
    baseline = TrainSearchResult(
        source_code="AAA", destination_code="CCC", date=DATE,
        trains=[make_train("900", from_code="AAA", to_code="CCC", distance=1000)],
    )
    leg1 = TrainSearchResult(
        source_code="AAA", destination_code="BPL", date=DATE,
        trains=[make_train(
            "L1", from_code="AAA", to_code="BPL",
            departure="08:00", duration_min=240,  # arrives BPL 12:00
            availability=[make_avail("3A", fare=1000, chance=95)],
        )],
    )
    leg2 = TrainSearchResult(
        source_code="BPL", destination_code="CCC", date=DATE,
        trains=[make_train(
            "L2", from_code="RKMP", to_code="CCC",  # departs a DIFFERENT station
            departure="13:00", duration_min=300,
            availability=[make_avail("3A", fare=1200, chance=95)],
        )],
    )
    train_provider = FakeTrainProvider({
        ("AAA", "CCC", DATE): baseline,
        ("AAA", "BPL", DATE): leg1,
        ("BPL", "CCC", DATE): leg2,
    })
    route_provider = FakeRouteProvider({
        "900": make_route("900", [
            ("AAA", 1, 0.0, True),
            ("BPL", 1, 400.0, True),
            ("CCC", 1, 1000.0, True),
        ]),
    })
    stations = StationService(FakeStationProvider())
    search = TrainSearchService(train_provider, stations)
    svc = SplitJourneyService(stations, search, RouteService(route_provider))

    itineraries = await svc.plan("AAA", "CCC", DATE)
    assert len(itineraries) == 1
    it = itineraries[0]
    assert it.transfer_arrival_station == "BPL"
    assert it.transfer_departure_station == "RKMP"
    assert it.requires_station_change is True
    assert it.legs[0].to_code == "BPL"
    assert it.legs[1].from_code == "RKMP"
    assert any("station change" in w.lower() for w in it.warnings)


def _overnight_split_service(leg1_train, leg2_train):
    """Wire a split service whose leg-2 candidate only exists on the NEXT day."""
    baseline = TrainSearchResult(
        source_code="AAA", destination_code="CCC", date=DATE,
        trains=[make_train("900", from_code="AAA", to_code="CCC", distance=1000)],
    )
    train_provider = FakeTrainProvider({
        ("AAA", "CCC", DATE): baseline,
        ("AAA", "BBB", DATE): TrainSearchResult(
            source_code="AAA", destination_code="BBB", date=DATE, trains=[leg1_train]
        ),
        ("BBB", "CCC", NEXT): TrainSearchResult(
            source_code="BBB", destination_code="CCC", date=NEXT, trains=[leg2_train]
        ),
    })
    route_provider = FakeRouteProvider({
        "900": make_route("900", [
            ("AAA", 1, 0.0, True),
            ("BBB", 1, 400.0, True),
            ("CCC", 1, 1000.0, True),
        ]),
    })
    stations = StationService(FakeStationProvider())
    search = TrainSearchService(train_provider, stations)
    return SplitJourneyService(stations, search, RouteService(route_provider))


async def test_split_journey_overnight_leg1_is_not_flagged_as_next_day():
    """Regression: leg 1 departs 22:00 and arrives 06:00 the NEXT day, and leg 2
    departs 08:00 that same next day. The day comparison must use leg 1's arrival
    day, not its departure day, so this is a same-day connection - not 'the day
    after the first leg arrives'."""
    svc = _overnight_split_service(
        make_train(
            "L1", from_code="AAA", to_code="BBB",
            departure="22:00", arrival="06:00", duration_min=480,
            availability=[make_avail("3A", fare=1000, chance=100)],
        ),
        make_train(
            "L2", from_code="BBB", to_code="CCC",
            departure="08:00", arrival="13:00", duration_min=300,
            availability=[make_avail("3A", fare=1200, chance=100)],
        ),
    )
    itineraries = await svc.plan("AAA", "CCC", DATE)
    assert len(itineraries) == 1
    it = itineraries[0]
    assert it.layover_min == 120  # 06:00 -> 08:00, both on NEXT

    # The legs carry the real calendar dates, including leg 1's rolled-over arrival.
    assert it.legs[0].departure_date == DATE
    assert it.legs[0].arrival_date == NEXT
    assert it.legs[1].departure_date == NEXT

    assert not any(
        "day after the first leg arrives" in w for w in it.warnings
    ), it.warnings
    note = next(w for w in it.warnings if "overnight" in w.lower())
    assert NEXT in note
    assert "same day" in note.lower()


async def test_split_journey_warns_when_leg2_departs_day_after_arrival():
    """The next-day warning still fires when leg 2 really does depart a calendar
    day after leg 1 arrives (leg 1 lands 21:00 on the departure day)."""
    svc = _overnight_split_service(
        make_train(
            "L1", from_code="AAA", to_code="BBB",
            departure="18:00", arrival="21:00", duration_min=180,
            availability=[make_avail("3A", fare=1000, chance=100)],
        ),
        make_train(
            "L2", from_code="BBB", to_code="CCC",
            departure="01:00", arrival="06:00", duration_min=300,
            availability=[make_avail("3A", fare=1200, chance=100)],
        ),
    )
    itineraries = await svc.plan("AAA", "CCC", DATE)
    assert len(itineraries) == 1
    it = itineraries[0]
    assert it.layover_min == 240  # 21:00 -> 01:00 next day
    assert it.legs[0].arrival_date == DATE  # leg 1 does not cross midnight

    note = next(w for w in it.warnings if "day after the first leg arrives" in w)
    assert NEXT in note and DATE in note


def _multimodal(enable_bus: bool, bus_trips=None, bus_fail=False):
    trains = TrainSearchResult(
        source_code="NDLS", destination_code="MMCT", date=DATE,
        trains=[make_train("111", duration_min=900, availability=[make_avail("SL", fare=700, chance=55)])],
    )
    train_provider = FakeTrainProvider({("NDLS", "MMCT", DATE): trains})
    stations = StationService(FakeStationProvider())
    search = TrainSearchService(train_provider, stations)
    bus_provider = FakeBusProvider(bus_trips, fail=bus_fail)
    return MultiModalService(
        search, bus_provider=bus_provider, enable_bus=enable_bus, enable_flight=False
    )


async def test_search_buses_disabled_is_graceful():
    svc = _multimodal(enable_bus=False)
    trips, meta = await svc.search_buses("NDLS", "MMCT", DATE)
    assert trips == []
    assert meta["available"] is False


async def test_search_buses_failure_is_graceful():
    svc = _multimodal(enable_bus=True, bus_fail=True)
    trips, meta = await svc.search_buses("NDLS", "MMCT", DATE)
    assert trips == []
    assert meta["available"] is False
    assert "down" in meta["reason"]


async def test_plan_trip_normalizes_modes():
    bus = BusTrip(operator="RedBus Travels", bus_type="AC Sleeper", duration_min=1000, fare=1200)
    svc = _multimodal(enable_bus=True, bus_trips=[bus])
    options, meta = await svc.plan_trip("NDLS", "MMCT", DATE, modes={TransportMode.TRAIN, TransportMode.BUS})
    modes = {o.mode for o in options}
    assert TransportMode.TRAIN in modes
    assert TransportMode.BUS in modes
    assert meta["train"]["available"] is True
    assert meta["bus"]["available"] is True
    # Sorted by duration: train (900) before bus (1000)
    assert options[0].mode == TransportMode.TRAIN
