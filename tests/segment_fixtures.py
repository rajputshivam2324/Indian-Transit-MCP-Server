"""Frozen data for the booking-segment tests.

Routes and availability are snapshots of the real ConfirmTkt responses for trains 15708
(ASR -> KIR) and 12204 (ASR -> SHC) for 10-10-2026, trimmed to the stops the scenarios need
(every stop that can land in a candidate window is kept, with its real distance and times).
Predictions move from hour to hour upstream, which is exactly why tests run on a fixed copy.

Row format for routes: ``(code, stop_name, day, km, arrival, departure, is_major)``.
Pair format for availability: ``(from, to) -> dict(dep, arr, km, avail=[(class, status,
chance, fare), ...])`` where ``dep``/``arr`` are the times at the two ends of the pair.
"""

from __future__ import annotations

from tests.fakes import (
    FakeRouteProvider,
    FakeStationProvider,
    FakeTrainProvider,
    make_avail,
    make_schedule_route,
    make_train,
)
from transit_mcp.config import Settings
from transit_mcp.infra.cache import TTLCache
from transit_mcp.infra.http import HttpClient
from transit_mcp.models.domain import Train, TrainRoute, TrainSearchResult
from transit_mcp.providers.estimators import PassthroughConfirmationEstimator
from transit_mcp.server.app import Container
from transit_mcp.services.planning import MultiModalService, SplitJourneyService
from transit_mcp.services.routes import NearbyStationService, RouteService
from transit_mcp.services.segments import BookingSegmentService
from transit_mcp.services.stations import StationService
from transit_mcp.services.trains import (
    AvailabilityService,
    ConfirmationService,
    TrainSearchService,
)

DATE = "10-10-2026"

# Every tool the server exposed before find_best_booking_segment was added.
EXISTING_TOOLS = {
    "find_station_code",
    "search_trains",
    "get_seat_availability",
    "predict_confirmation",
    "get_train_route",
    "suggest_nearby_stations",
    "plan_split_journey",
    "search_buses",
    "search_flights",
    "plan_trip",
}

# --------------------------------------------------------------------------- #
# 15708 Amritsar -> Katihar. Runs ASR 07:40 (day 1) ... DLI 17:35 (day 1) ... MFP (day 2).
# GZB, DSA ... are pass-throughs (arrival == departure, not major): the train runs through.
# --------------------------------------------------------------------------- #
ROUTE_15708 = [
    ("ASR", "Amritsar Jn", 1, 0.0, None, "07:40", True),
    ("JUC", "Jalandhar City", 1, 79.0, "08:48", "08:58", True),
    ("PGW", "Phagwara Jn", 1, 100.0, "09:21", "09:23", True),
    ("LDH", "Ludhiana Jn", 1, 136.0, "10:05", "10:15", True),
    ("UMB", "Ambala Cant Jn", 1, 250.0, "12:50", "13:00", True),
    ("KUN", "Karnal", 1, 324.0, "13:48", "13:50", True),
    ("PNP", "Panipat Jn", 1, 359.0, "14:12", "14:14", True),
    ("BDMJ", "Bhodwal Majri", 1, 382.0, "14:32", "14:34", True),
    ("SNP", "Sonipat", 1, 404.0, "14:56", "14:58", True),
    ("SZM", "Subzi Mandi", 1, 444.0, "16:30", "16:32", True),
    ("DLI", "Old Delhi", 1, 447.0, "17:20", "17:35", True),
    ("DSA", "Delhi Shahdara", 1, 449.0, "17:42", "17:42", False),
    ("SBB", "Sahibabad", 1, 455.0, "17:49", "17:49", False),
    ("GZB", "Ghaziabad Jn", 1, 462.0, "17:57", "17:57", False),
    ("ETW", "Etawah Jn", 1, 742.0, "21:55", "21:57", True),
    ("CNB", "Kanpur Central", 2, 882.0, "00:25", "00:30", True),
    ("GKP", "Gorakhpur Jn", 2, 1230.0, "08:10", "08:20", True),
    ("HJP", "Hajipur Jn", 2, 1469.0, "13:30", "13:35", True),
    ("MFP", "Muzaffarpur Jn", 2, 1523.0, "14:55", "15:05", True),
    ("DOL", "Dholi", 2, 1548.0, "15:39", "15:41", True),
    ("KRBP", "Khudiram B Pusa", 2, 1562.0, "16:08", "16:10", True),
    ("SPJ", "Samastipur Jn", 2, 1575.0, "16:50", "16:55", True),
    ("BJU", "Barauni Jn", 2, 1626.0, "18:05", "18:15", True),
    ("BGS", "Begu Sarai", 2, 1641.0, "18:31", "18:33", True),
    ("KGG", "Khagaria Jn", 2, 1681.0, "19:15", "19:17", True),
    ("KIR", "Katihar Jn", 2, 1805.0, "22:10", None, True),
]

PAIRS_15708 = {
    ("DLI", "MFP"): dict(
        dep="17:35",
        arr="14:55",
        km=1077,
        avail=[
            ("SL", "RLWL59/WL38", 72, 540),
            ("3E", "RLWL21/WL15", 65, 1320),
            ("3A", "RLWL21/WL13", 72, 1420),
            ("2A", "RLWL14/WL13", 69, 2015),
        ],
    ),
    ("ASR", "MFP"): dict(
        dep="07:40",
        arr="14:55",
        km=1522,
        avail=[
            ("SL", "GNWL127/WL60", 78, 670),
            ("3E", "GNWL36/WL3", 96, 1630),
            ("3A", "GNWL34/WL19", 87, 1735),
            ("2A", "GNWL8/WL7", 88, 2480),
        ],
    ),
    ("ASR", "KIR"): dict(
        dep="07:40",
        arr="22:10",
        km=1804,
        avail=[("3E", "GNWL40/WL6", 95, 1855), ("3A", "GNWL34/WL19", 88, 1960)],
    ),
    ("SZM", "MFP"): dict(
        dep="16:32", arr="14:55", km=1079, avail=[("3A", "RLWL26/WL12", 82, 1420)]
    ),
    ("SNP", "MFP"): dict(
        dep="14:58", arr="14:55", km=1119, avail=[("3A", "RLWL20/WL10", 80, 1435)]
    ),
    # BDMJ -> MFP exists but is not sold in 3A: it must count as "unavailable" for a 3A search.
    ("BDMJ", "MFP"): dict(
        dep="14:34", arr="14:55", km=1141, avail=[("SL", "RLWL70/WL40", 60, 545)]
    ),
    ("PNP", "MFP"): dict(
        dep="14:14", arr="14:55", km=1164, avail=[("3A", "RLWL28/WL14", 75, 1475)]
    ),
    # The 7th-ranked candidate: outside the default budget of 6.
    ("KUN", "MFP"): dict(
        dep="13:50", arr="14:55", km=1199, avail=[("3A", "RLWL28/WL14", 76, 1490)]
    ),
    ("DLI", "DOL"): dict(
        dep="17:35", arr="15:41", km=1102, avail=[("3A", "RLWL17/WL11", 56, 1435)]
    ),
}

# --------------------------------------------------------------------------- #
# 12204 Amritsar -> Saharsa (Garib Rath, 3A only). SZM is a pass-through here, unlike 15708.
# --------------------------------------------------------------------------- #
ROUTE_12204 = [
    ("ASR", "Amritsar Jn", 1, 0.0, None, "04:00", True),
    ("BEAS", "Beas", 1, 43.0, "04:28", "04:30", True),
    ("JUC", "Jalandhar City", 1, 79.0, "05:05", "05:10", True),
    ("PGW", "Phagwara Jn", 1, 100.0, "05:28", "05:30", True),
    ("DDL", "Dhandari Kalan", 1, 143.0, "06:20", "06:30", True),
    ("UMB", "Ambala Cant Jn", 1, 250.0, "08:00", "08:10", True),
    ("SZM", "Subzi Mandi", 1, 444.0, "10:48", "10:48", False),
    ("DLI", "Old Delhi", 1, 447.0, "10:50", "11:05", True),
    ("GZB", "Ghaziabad Jn", 1, 462.0, "11:30", "11:30", False),
    ("HPU", "Hapur", 1, 504.0, "12:16", "12:18", True),
    ("MB", "Moradabad", 1, 608.0, "13:48", "13:53", True),
    ("BE", "Bareilly", 1, 698.0, "15:11", "15:13", True),
    ("HRI", "Hardoi", 1, 831.0, "16:51", "16:53", True),
    ("LKO", "Lucknow Nr", 1, 933.0, "18:30", "18:40", True),
    ("GKP", "Gorakhpur Jn", 2, 1212.0, "00:02", "00:12", True),
    ("DEOS", "Deoria Sadar", 2, 1261.0, "01:08", "01:10", True),
    ("SV", "Siwan Jn", 2, 1331.0, "02:00", "02:05", True),
    ("CPR", "Chhapra", 2, 1391.0, "03:05", "03:10", True),
    ("HJP", "Hajipur Jn", 2, 1451.0, "04:27", "04:32", True),
    ("MFP", "Muzaffarpur Jn", 2, 1504.0, "05:15", "05:20", True),
    ("SPJ", "Samastipur Jn", 2, 1556.0, "06:20", "06:25", True),
    ("DSS", "Dalsingh Sarai", 2, 1580.0, "06:38", "06:40", True),
    ("BJU", "Barauni Jn", 2, 1607.0, "07:35", "07:45", True),
    ("BGS", "Begu Sarai", 2, 1622.0, "08:01", "08:03", True),
    ("KGG", "Khagaria Jn", 2, 1663.0, "08:36", "08:38", True),
    ("SBV", "S Bakhtiyarpur", 2, 1699.0, "10:16", "10:18", True),
    ("SHC", "Saharsa Jn", 2, 1716.0, "11:20", None, True),
]


def pairs_12204(asr_shc: tuple[str, int, int] = ("GNWL116/WL54", 79, 1305)) -> dict:
    """12204 availability; ``asr_shc`` is (status, chance, fare) because that pair's
    prediction is the one that moves most (79% in the user's snapshot, 90% an hour later)."""
    status, chance, fare = asr_shc
    return {
        ("DLI", "MFP"): dict(
            dep="11:05", arr="05:15", km=1058, avail=[("3A", "RLWL99/WL64", 74, 1015)]
        ),
        ("ASR", "MFP"): dict(
            dep="04:00", arr="05:15", km=1503, avail=[("3A", "GNWL116/WL54", 83, 1215)]
        ),
        ("ASR", "SHC"): dict(
            dep="04:00", arr="11:20", km=1715, avail=[("3A", status, chance, fare)]
        ),
        ("UMB", "MFP"): dict(
            dep="08:10", arr="05:15", km=1255, avail=[("3A", "RLWL73/WL47", 76, 1115)]
        ),
        ("DDL", "MFP"): dict(
            dep="06:30", arr="05:15", km=1362, avail=[("3A", "RLWL73/WL47", 78, 1150)]
        ),
        ("PGW", "MFP"): dict(
            dep="05:30", arr="05:15", km=1405, avail=[("3A", "PQWL52/WL29", 85, 1165)]
        ),
        ("JUC", "MFP"): dict(
            dep="05:10", arr="05:15", km=1426, avail=[("3A", "PQWL52/WL29", 86, 1165)]
        ),
        # Outside the default budget of 6 (7th), reachable with a larger max_candidates.
        ("BEAS", "MFP"): dict(
            dep="04:30", arr="05:15", km=1462, avail=[("3A", "GNWL116/WL54", 75, 1200)]
        ),
    }


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def route_15708(**kw) -> TrainRoute:
    return make_schedule_route("15708", "Asr Kir Express", ROUTE_15708, **kw)


def route_12204(**kw) -> TrainRoute:
    return make_schedule_route("12204", "Shc Garib Rath", ROUTE_12204, **kw)


def make_provider(specs: list[tuple[str, str, dict]], *, date: str = DATE) -> FakeTrainProvider:
    """A fake train provider from ``(train_number, name, pairs)`` specs.

    Trains that share a (from, to, date) land in the same search result, like upstream.
    A pair may override ``date`` (date-shift scenarios) and ``from`` / ``to`` (a nearby-station
    variant listed under the requested corridor).
    """
    buckets: dict[tuple[str, str, str], list[Train]] = {}
    for number, name, pairs in specs:
        for (src, dst), info in pairs.items():
            key = (src, dst, info.get("date", date))
            train = make_train(
                number,
                name=name,
                from_code=info.get("from", src),
                to_code=info.get("to", dst),
                departure=info["dep"],
                arrival=info["arr"],
                distance=info["km"],
                availability=[
                    make_avail(cls, fare=fare, chance=chance, status=status)
                    for cls, status, chance, fare in info["avail"]
                ],
            )
            buckets.setdefault(key, []).append(train)
    return FakeTrainProvider(
        {
            key: TrainSearchResult(
                source_code=key[0], destination_code=key[1], date=key[2], trains=trains
            )
            for key, trains in buckets.items()
        }
    )


def make_service(
    provider: FakeTrainProvider,
    routes: dict[str, TrainRoute],
    *,
    cached: bool = False,
    estimator=None,
) -> BookingSegmentService:
    stations = StationService(FakeStationProvider())
    search = TrainSearchService(
        provider,
        stations,
        cache=TTLCache(120) if cached else None,
        cache_ttl=120 if cached else None,
    )
    return BookingSegmentService(
        stations,
        search,
        RouteService(FakeRouteProvider(routes)),
        estimator or PassthroughConfirmationEstimator(),
    )


def service_15708(**kw) -> tuple[BookingSegmentService, FakeTrainProvider]:
    provider = make_provider([("15708", "ASR KIR EXPRESS", PAIRS_15708)])
    return make_service(provider, {"15708": route_15708()}, **kw), provider


def service_12204(asr_shc=("GNWL116/WL54", 79, 1305), **kw):
    provider = make_provider([("12204", "SHC GARIB RATH", pairs_12204(asr_shc))])
    return make_service(provider, {"12204": route_12204()}, **kw), provider


def make_container(*, with_segments: bool = True) -> Container:
    """A full Container of in-memory fakes loaded with the frozen 15708 / 12204 data.

    Assembled by hand, as older tests do: with ``with_segments=False`` the optional
    ``segments`` field stays unset and the tool has to build the service itself. Settings
    ignore any local ``.env`` so a developer's own config cannot leak into tests.
    """
    provider = make_provider(
        [("15708", "ASR KIR EXPRESS", PAIRS_15708), ("12204", "SHC GARIB RATH", pairs_12204())]
    )
    stations = StationService(FakeStationProvider())
    train_search = TrainSearchService(provider, stations)
    availability = AvailabilityService(train_search)
    routes = RouteService(FakeRouteProvider({"15708": route_15708(), "12204": route_12204()}))
    segments = (
        BookingSegmentService(stations, train_search, routes, PassthroughConfirmationEstimator())
        if with_segments
        else None
    )
    return Container(
        settings=Settings(_env_file=None),
        http=HttpClient("https://unused.test", max_retries=0),
        stations=stations,
        train_search=train_search,
        availability=availability,
        confirmation=ConfirmationService(availability, PassthroughConfirmationEstimator()),
        routes=routes,
        nearby=NearbyStationService(stations),
        split=SplitJourneyService(stations, train_search, routes),
        multimodal=MultiModalService(train_search),
        segments=segments,
    )
