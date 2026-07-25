"""Provider ports (structural interfaces).

Services depend only on these ``Protocol`` types, never on concrete providers.
Concrete implementations (ConfirmTkt, bus, flight) are injected at the composition
root. Ports return **domain models** so that in-memory fakes used by tests are
trivial to write and services never touch mapping/HTTP concerns (DIP + ISP).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models.domain import (
    BusTrip,
    ClassAvailability,
    Flight,
    Station,
    Train,
    TrainRoute,
    TrainSearchResult,
)


@runtime_checkable
class StationProvider(Protocol):
    """Resolve free-text queries to candidate stations."""

    async def autosuggest(self, query: str) -> list[Station]: ...


@runtime_checkable
class TrainProvider(Protocol):
    """Search direct trains for a corridor and date (DD-MM-YYYY)."""

    async def search(self, src: str, dst: str, date: str) -> TrainSearchResult: ...


@runtime_checkable
class RouteProvider(Protocol):
    """Fetch the full ordered route/schedule for a train."""

    async def route(self, train_number: str, date: str | None = None) -> TrainRoute: ...


@runtime_checkable
class BusProvider(Protocol):
    """Search bus options for a corridor and date."""

    async def search(self, src: str, dst: str, date: str) -> list[BusTrip]: ...


@runtime_checkable
class FlightProvider(Protocol):
    """Search flight options for a corridor and date."""

    async def search(self, src: str, dst: str, date: str) -> list[Flight]: ...


@runtime_checkable
class ConfirmationEstimator(Protocol):
    """Estimate/refine confirmation chance for a class availability.

    The default implementation is a passthrough over ConfirmTkt's own prediction;
    a heuristic or ML estimator can replace it without touching callers (OCP).
    """

    def estimate(self, availability: ClassAvailability) -> ClassAvailability: ...
