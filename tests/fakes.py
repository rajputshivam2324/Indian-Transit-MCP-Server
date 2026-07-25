"""In-memory fake providers implementing the ports (no network).

Because the ports return domain models, fakes are trivial. That is the payoff of
depending on abstractions (DIP).
"""

from __future__ import annotations

from transit_mcp.infra.errors import NoResultsError
from transit_mcp.models.domain import (
    BusTrip,
    Flight,
    Station,
    TrainRoute,
    TrainSearchResult,
)


class FakeStationProvider:
    def __init__(self, mapping: dict[str, list[Station]] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    async def autosuggest(self, query: str) -> list[Station]:
        self.calls.append(query)
        key = query.strip().lower()
        if key in self.mapping:
            return self.mapping[key]
        # Default: echo the query as a station code.
        return [Station(code=query.strip().upper(), name=query.strip().title(), is_major=True)]


class FakeTrainProvider:
    def __init__(self, results: dict[tuple[str, str, str], TrainSearchResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, str, str]] = []

    async def search(self, src: str, dst: str, date: str) -> TrainSearchResult:
        self.calls.append((src, dst, date))
        key = (src.upper(), dst.upper(), date)
        if key in self.results:
            return self.results[key]
        return TrainSearchResult(source_code=src.upper(), destination_code=dst.upper(), date=date)


class FakeRouteProvider:
    def __init__(self, routes: dict[str, TrainRoute] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[str] = []

    async def route(self, train_number: str, date: str | None = None) -> TrainRoute:
        self.calls.append(train_number)
        if train_number in self.routes:
            return self.routes[train_number]
        raise NoResultsError(f"no route for {train_number}")


class FakeBusProvider:
    def __init__(self, trips: list[BusTrip] | None = None, fail: bool = False) -> None:
        self.trips = trips or []
        self.fail = fail

    async def search(self, src: str, dst: str, date: str) -> list[BusTrip]:
        if self.fail:
            from transit_mcp.infra.errors import UpstreamUnavailableError

            raise UpstreamUnavailableError("bus down", provider="bus")
        return self.trips


class FakeFlightProvider:
    def __init__(self, flights: list[Flight] | None = None, fail: bool = False) -> None:
        self.flights = flights or []
        self.fail = fail

    async def search(self, src: str, dst: str, date: str) -> list[Flight]:
        if self.fail:
            from transit_mcp.infra.errors import UpstreamUnavailableError

            raise UpstreamUnavailableError("flight down", provider="flight")
        return self.flights


# --------------------------------------------------------------------------- #
# Builders for synthetic domain objects
# --------------------------------------------------------------------------- #
from transit_mcp.models.domain import ClassAvailability, Stop, Train  # noqa: E402


def make_avail(
    travel_class: str = "SL",
    *,
    fare: int | None = 500,
    chance: int | None = 60,
    status: str = "Confirm",
    seats: int | None = None,
    quota: str = "GN",
) -> ClassAvailability:
    return ClassAvailability(
        travel_class=travel_class,
        quota=quota,
        status=status,
        status_display=status,
        seats=seats,
        fare=fare,
        confirm_status=status,
        confirm_chance=chance,
    )


def make_train(
    number: str,
    *,
    name: str = "Test Exp",
    from_code: str = "AAA",
    to_code: str = "BBB",
    departure: str = "10:00",
    arrival: str = "18:00",
    duration_min: int = 480,
    distance: int | None = 500,
    availability: list[ClassAvailability] | None = None,
) -> Train:
    return Train(
        number=number,
        name=name,
        from_code=from_code,
        to_code=to_code,
        departure=departure,
        arrival=arrival,
        duration_min=duration_min,
        distance=distance,
        availability=availability if availability is not None else [make_avail()],
    )


def make_route(number: str, codes_days_dist_major: list[tuple]) -> TrainRoute:
    """Build a route from tuples of (code, day, distance, is_major)."""
    stops = [
        Stop(
            code=c,
            name=c,
            arrival="10:00",
            departure="10:05",
            day=day,
            distance_from_origin=dist,
            is_major=major,
        )
        for (c, day, dist, major) in codes_days_dist_major
    ]
    return TrainRoute(
        number=number,
        name=f"Train {number}",
        from_code=stops[0].code if stops else "",
        to_code=stops[-1].code if stops else "",
        total_duration_min=600,
        stops=stops,
    )
