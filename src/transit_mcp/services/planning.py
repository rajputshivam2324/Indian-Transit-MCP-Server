"""Advanced planning: single-transfer split journeys and multi-modal comparison."""

from __future__ import annotations

import asyncio

from ..common import add_days
from ..infra.errors import TransitError
from ..infra.logging import get_logger
from ..interfaces.providers import BusProvider, FlightProvider
from ..models.domain import (
    BusTrip,
    ClassAvailability,
    Flight,
    JourneyLeg,
    SplitItinerary,
    Train,
    TripOption,
)
from ..models.enums import SortBy, TransportMode
from .ranking import (
    best_confirm_chance,
    class_is_available,
    departure_minutes,
    min_fare,
)
from .routes import RouteService
from .stations import StationService
from .trains import TrainSearchService

log = get_logger("planning")

# Bounds that keep the fan-out small and predictable.
_HUB_ROUTE_SAMPLE = 5  # direct trains whose routes we scan for hubs
_LEG_CANDIDATES = 6  # trains kept per leg before pairing
_MINUTES_PER_DAY = 1440

# Feasibility thresholds used to raise (not enforce) warnings.
_TIGHT_LAYOVER_MIN = 45  # same-station transfers tighter than this get a note
_STATION_CHANGE_BUFFER_MIN = 60  # recommended minimum when changing stations


def _index_of(codes: list[str], *candidates: str, default: int | None = None) -> int | None:
    """Return the index of the first candidate code present in ``codes``."""
    for code in candidates:
        if code and code in codes:
            return codes.index(code)
    return default


def _best_class(train: Train, classes: set[str] | None):
    pool = train.availability
    if classes:
        pool = [a for a in pool if a.travel_class in classes] or train.availability
    ranked = sorted(
        pool,
        key=lambda a: (a.confirm_chance if a.confirm_chance is not None else -1),
        reverse=True,
    )
    return ranked[0] if ranked else None


def _paired_class(
    leg1: Train, leg2: Train, classes: set[str] | None
) -> tuple[ClassAvailability | None, ClassAvailability | None]:
    """Pick one travel class available on BOTH legs, maximizing the weakest leg's
    confirmation, so the itinerary is a coherent single-class journey whose fare and
    combined chance are internally consistent.
    """

    def by_class(train: Train) -> dict[str, ClassAvailability]:
        out: dict[str, ClassAvailability] = {}
        for a in train.availability:
            if classes and a.travel_class not in classes:
                continue
            out.setdefault(a.travel_class, a)  # first entry = general quota
        return out

    m1, m2 = by_class(leg1), by_class(leg2)
    shared = set(m1) & set(m2)
    if not shared:
        return None, None

    def score(cls: str) -> tuple[int, int]:
        c1 = m1[cls].confirm_chance if m1[cls].confirm_chance is not None else -1
        c2 = m2[cls].confirm_chance if m2[cls].confirm_chance is not None else -1
        return (min(c1, c2), c1 + c2)  # maximize the weakest leg, then the pair

    best = max(shared, key=score)
    return m1[best], m2[best]


class SplitJourneyService:
    """Build ranked single-transfer itineraries via hub stations."""

    def __init__(
        self,
        station_service: StationService,
        train_search: TrainSearchService,
        route_service: RouteService,
        *,
        max_hubs: int = 6,
        top_n: int = 5,
        default_max_layover_hours: float = 6.0,
        default_min_transfer_min: int = 30,
    ) -> None:
        self._stations = station_service
        self._search = train_search
        self._routes = route_service
        self._max_hubs = max_hubs
        self._top_n = top_n
        self._default_max_layover_hours = default_max_layover_hours
        self._default_min_transfer_min = default_min_transfer_min

    async def plan(
        self,
        origin: str,
        destination: str,
        date: str,
        *,
        max_layover_hours: float | None = None,
        min_transfer_min: int | None = None,
        classes: set[str] | None = None,
        min_confirm_chance: int | None = None,
    ) -> list[SplitItinerary]:
        max_layover = (max_layover_hours or self._default_max_layover_hours) * 60
        min_transfer = (
            min_transfer_min
            if min_transfer_min is not None
            else self._default_min_transfer_min
        )

        src, dst = await asyncio.gather(
            self._stations.resolve_code(origin),
            self._stations.resolve_code(destination),
        )

        hubs = await self._derive_hubs(src, dst, date)
        if not hubs:
            return []

        itineraries: list[SplitItinerary] = []
        per_hub = await asyncio.gather(
            *(self._plan_via_hub(src, dst, hub, date, classes, min_confirm_chance) for hub in hubs),
            return_exceptions=True,
        )
        for hub, result in zip(hubs, per_hub):
            if isinstance(result, Exception):
                log.info("hub %s failed: %s", hub, result)
                continue
            for leg1, leg2, offset in result:
                itin = self._build_itinerary(
                    src, dst, hub, leg1, leg2, offset, classes, min_transfer, max_layover
                )
                if itin is not None:
                    itineraries.append(itin)

        # Rank by combined confirmation first (the usual goal is a confirmed seat),
        # then prefer same-station transfers, then shorter journeys/layovers.
        itineraries.sort(
            key=lambda it: (
                -(it.combined_confirm_chance if it.combined_confirm_chance is not None else -1),
                it.requires_station_change,
                it.total_duration_min,
                it.layover_min,
            )
        )
        # De-duplicate identical train pairs, keep best-scored first.
        seen: set[tuple[str, str, str]] = set()
        unique: list[SplitItinerary] = []
        for it in itineraries:
            key = (it.legs[0].train.number, it.transfer_station, it.legs[1].train.number)
            if key in seen:
                continue
            seen.add(key)
            unique.append(it)
        return unique[: self._top_n]

    async def _derive_hubs(self, src: str, dst: str, date: str) -> list[str]:
        """Rank major junctions that lie between src and dst across sample routes."""
        try:
            baseline = await self._search.raw_search_by_code(src, dst, date)
        except TransitError as exc:
            log.info("baseline search for hubs failed: %s", exc)
            return []

        sample = sorted(
            baseline.trains,
            key=lambda t: (t.distance or 0),
            reverse=True,
        )[:_HUB_ROUTE_SAMPLE]

        routes = await asyncio.gather(
            *(self._routes.get_route(t.number, date) for t in sample),
            return_exceptions=True,
        )

        freq: dict[str, int] = {}
        dist: dict[str, float] = {}
        for train, route in zip(sample, routes):
            if isinstance(route, Exception):
                continue
            codes = [s.code for s in route.stops]

            # "Nearby" search may return trains whose endpoints are variants of the
            # requested stations (e.g. NZM for NDLS, BVI for BCT). Bound the search
            # region by the requested codes when present, else the train's own ends.
            i = _index_of(codes, src, train.from_code, default=0)
            j = _index_of(codes, dst, train.to_code, default=len(codes) - 1)
            if i is None or j is None or i >= j:
                continue

            exclude = {src, dst, train.from_code, train.to_code}
            for stop in route.stops[i + 1 : j]:
                if stop.is_major and stop.code not in exclude:
                    freq[stop.code] = freq.get(stop.code, 0) + 1
                    if stop.distance_from_origin is not None:
                        dist.setdefault(stop.code, stop.distance_from_origin)

        ranked = sorted(freq, key=lambda c: (freq[c], -dist.get(c, 0.0)), reverse=True)
        return ranked[: self._max_hubs]

    async def _plan_via_hub(
        self,
        src: str,
        dst: str,
        hub: str,
        date: str,
        classes: set[str] | None,
        min_confirm_chance: int | None,
    ) -> list[tuple[Train, Train, int]]:
        next_day = add_days(date, 1)
        leg1_res, leg2_today, leg2_next = await asyncio.gather(
            self._search.raw_search_by_code(src, hub, date),
            self._search.raw_search_by_code(hub, dst, date),
            self._search.raw_search_by_code(hub, dst, next_day),
            return_exceptions=True,
        )
        if isinstance(leg1_res, Exception):
            return []

        leg1_trains = self._filter_leg(leg1_res.trains, classes, min_confirm_chance)
        leg2_trains: list[tuple[Train, int]] = []
        if not isinstance(leg2_today, Exception):
            leg2_trains += [
                (t, 0)
                for t in self._filter_leg(leg2_today.trains, classes, min_confirm_chance)
            ]
        if not isinstance(leg2_next, Exception):
            leg2_trains += [
                (t, _MINUTES_PER_DAY)
                for t in self._filter_leg(leg2_next.trains, classes, min_confirm_chance)
            ]

        pairs: list[tuple[Train, Train, int]] = []
        for leg1 in leg1_trains:
            for leg2, offset in leg2_trains:
                pairs.append((leg1, leg2, offset))
        return pairs

    def _filter_leg(
        self, trains: list[Train], classes: set[str] | None, min_confirm_chance: int | None
    ) -> list[Train]:
        out = []
        for t in trains:
            best = _best_class(t, classes)
            if best is None or not class_is_available(best):
                continue
            if min_confirm_chance is not None:
                if best.confirm_chance is None or best.confirm_chance < min_confirm_chance:
                    continue
            out.append(t)
        out.sort(key=lambda t: -(best_confirm_chance(t, classes) or -1))
        return out[:_LEG_CANDIDATES]

    def _build_itinerary(
        self,
        src: str,
        dst: str,
        hub: str,
        leg1: Train,
        leg2: Train,
        offset: int,
        classes: set[str] | None,
        min_transfer: int,
        max_layover: float,
    ) -> SplitItinerary | None:
        leg1_dep = departure_minutes(leg1)
        leg1_arr = leg1_dep + (leg1.duration_min or 0)
        leg2_dep = offset + departure_minutes(leg2)
        leg2_arr = leg2_dep + (leg2.duration_min or 0)

        layover = leg2_dep - leg1_arr
        if layover < min_transfer or layover > max_layover:
            return None

        # Prefer a class available on both legs so the fare and confirmation are for a
        # single, bookable class. Fall back to each leg's best class only if none is shared.
        a1, a2 = _paired_class(leg1, leg2, classes)
        same_class = a1 is not None and a2 is not None
        if not same_class:
            a1 = _best_class(leg1, classes)
            a2 = _best_class(leg2, classes)

        itinerary_class = a1.travel_class if (same_class and a1 is not None) else None

        # Fare and combined chance are always derived from the SAME availabilities shown
        # on the legs, so total_fare == leg1.fare + leg2.fare (never a cheaper class).
        fare = None
        if a1 and a2 and a1.fare is not None and a2.fare is not None:
            fare = a1.fare + a2.fare
        combined = None
        if a1 and a2 and a1.confirm_chance is not None and a2.confirm_chance is not None:
            combined = round(a1.confirm_chance / 100 * a2.confirm_chance / 100 * 100, 1)

        # Use the trains' real endpoints: the "nearby" search can make leg 1 arrive
        # at one station and leg 2 depart another in the same city.
        arrival_station = leg1.to_code or hub
        departure_station = leg2.from_code or hub
        requires_change = bool(
            arrival_station and departure_station and arrival_station != departure_station
        )

        warnings = self._build_warnings(
            leg1, leg2, a1, a2, layover, offset, arrival_station, departure_station, requires_change
        )
        if not same_class and a1 is not None and a2 is not None:
            warnings.append(
                f"Legs use different classes (leg 1 {a1.travel_class}, leg 2 "
                f"{a2.travel_class}); no single class is available on both, so "
                f"total_fare covers the two different classes."
            )

        return SplitItinerary(
            legs=[
                JourneyLeg(
                    train=leg1,
                    from_code=leg1.from_code or src,
                    to_code=arrival_station,
                    departure_date=leg1.departure_date,
                    availability=a1,
                ),
                JourneyLeg(
                    train=leg2,
                    from_code=departure_station,
                    to_code=leg2.to_code or dst,
                    departure_date=leg2.departure_date,
                    availability=a2,
                ),
            ],
            transfer_station=hub,
            transfer_arrival_station=arrival_station,
            transfer_departure_station=departure_station,
            requires_station_change=requires_change,
            travel_class=itinerary_class,
            total_duration_min=leg2_arr - leg1_dep,
            layover_min=layover,
            total_fare=fare,
            combined_confirm_chance=combined,
            warnings=warnings,
        )

    @staticmethod
    def _build_warnings(
        leg1: Train,
        leg2: Train,
        a1,
        a2,
        layover: int,
        offset: int,
        arrival_station: str,
        departure_station: str,
        requires_change: bool,
    ) -> list[str]:
        warnings: list[str] = []
        if requires_change:
            warnings.append(
                f"Station change at transfer: arrive {arrival_station}, depart "
                f"{departure_station} (different stations in the same city, so leave "
                f"time to travel between them)."
            )
            if layover < _STATION_CHANGE_BUFFER_MIN:
                warnings.append(
                    f"Layover of {layover} min is likely too short for a station "
                    f"change (recommend at least {_STATION_CHANGE_BUFFER_MIN} min)."
                )
        elif layover < _TIGHT_LAYOVER_MIN:
            warnings.append(f"Tight layover of {layover} min at {arrival_station}.")

        if offset >= _MINUTES_PER_DAY:
            warnings.append("Second leg departs the day after the first leg arrives.")

        for idx, leg, avail in ((1, leg1, a1), (2, leg2, a2)):
            if avail and avail.confirm_chance is not None and avail.confirm_chance < 100:
                status = avail.status_display or avail.status or "waitlisted"
                warnings.append(
                    f"Leg {idx} ({leg.number} {avail.travel_class}) not confirmed: "
                    f"{status}, {avail.confirm_chance}% chance."
                )
        return warnings


class MultiModalService:
    """Compare trains against (optional) buses and flights as normalized rows."""

    def __init__(
        self,
        train_search: TrainSearchService,
        *,
        bus_provider: BusProvider | None = None,
        flight_provider: FlightProvider | None = None,
        enable_bus: bool = False,
        enable_flight: bool = False,
    ) -> None:
        self._search = train_search
        self._bus = bus_provider
        self._flight = flight_provider
        self._enable_bus = enable_bus
        self._enable_flight = enable_flight

    async def search_buses(
        self, origin: str, destination: str, date: str
    ) -> tuple[list[BusTrip], dict]:
        if not self._enable_bus or self._bus is None:
            return [], {"available": False, "reason": "bus search disabled"}
        try:
            trips = await self._bus.search(origin, destination, date)
            return trips, {"available": True, "count": len(trips)}
        except TransitError as exc:
            log.info("bus search failed: %s", exc)
            return [], {"available": False, "reason": str(exc)}

    async def search_flights(
        self, origin: str, destination: str, date: str
    ) -> tuple[list[Flight], dict]:
        if not self._enable_flight or self._flight is None:
            return [], {"available": False, "reason": "flight search disabled"}
        try:
            flights = await self._flight.search(origin, destination, date)
            return flights, {"available": True, "count": len(flights)}
        except TransitError as exc:
            log.info("flight search failed: %s", exc)
            return [], {"available": False, "reason": str(exc)}

    async def plan_trip(
        self,
        origin: str,
        destination: str,
        date: str,
        modes: set[TransportMode] | None = None,
    ) -> tuple[list[TripOption], dict]:
        modes = modes or {TransportMode.TRAIN, TransportMode.BUS, TransportMode.FLIGHT}
        meta: dict[str, dict] = {}
        options: list[TripOption] = []

        tasks: dict[str, asyncio.Future] = {}
        if TransportMode.TRAIN in modes:
            tasks["train"] = asyncio.ensure_future(
                self._search.search(origin, destination, date, sort_by=SortBy.DURATION, limit=5)
            )
        if TransportMode.BUS in modes:
            tasks["bus"] = asyncio.ensure_future(self.search_buses(origin, destination, date))
        if TransportMode.FLIGHT in modes:
            tasks["flight"] = asyncio.ensure_future(self.search_flights(origin, destination, date))

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        by_mode = dict(zip(tasks.keys(), results))

        if "train" in by_mode:
            res = by_mode["train"]
            if isinstance(res, Exception):
                meta["train"] = {"available": False, "reason": str(res)}
            else:
                meta["train"] = {"available": True, "count": len(res.trains)}
                for t in res.trains:
                    options.append(
                        TripOption(
                            mode=TransportMode.TRAIN,
                            summary=f"{t.number} {t.name} {t.departure}->{t.arrival}",
                            total_duration_min=t.duration_min or None,
                            price=min_fare(t),
                            confirm_chance=best_confirm_chance(t),
                            detail=t.model_dump(mode="json"),
                        )
                    )

        if "bus" in by_mode and not isinstance(by_mode["bus"], Exception):
            trips, m = by_mode["bus"]
            meta["bus"] = m
            for b in trips:
                options.append(
                    TripOption(
                        mode=TransportMode.BUS,
                        summary=f"{b.operator} {b.bus_type or ''}".strip(),
                        total_duration_min=b.duration_min,
                        price=b.fare,
                        detail=b.model_dump(mode="json"),
                    )
                )

        if "flight" in by_mode and not isinstance(by_mode["flight"], Exception):
            flights, m = by_mode["flight"]
            meta["flight"] = m
            for fl in flights:
                options.append(
                    TripOption(
                        mode=TransportMode.FLIGHT,
                        summary=f"{fl.airline} {fl.flight_number or ''}".strip(),
                        total_duration_min=fl.duration_min,
                        price=fl.fare,
                        detail=fl.model_dump(mode="json"),
                    )
                )

        options.sort(
            key=lambda o: (
                o.total_duration_min if o.total_duration_min is not None else float("inf")
            )
        )
        return options, meta
