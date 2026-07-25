"""Train-centric services: search, seat availability, confirmation prediction."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from ..infra.cache import TTLCache
from ..infra.errors import NoResultsError
from ..interfaces.providers import ConfirmationEstimator, TrainProvider
from ..models.domain import ClassAvailability, Train, TrainSearchResult
from ..models.enums import SortBy
from .ranking import (
    TrainFilters,
    apply_preferred,
    filter_trains,
    sort_trains,
)
from .stations import StationService


class SearchResponse(BaseModel):
    """Final, client-facing train search result."""

    origin: str
    destination: str
    date: str
    source_name: str | None = None
    destination_name: str | None = None
    quotas: list[str] = Field(default_factory=list)
    total_matched: int = 0
    trains: list[Train] = Field(default_factory=list)
    alternatives: list[Train] = Field(default_factory=list)
    from_cache: bool = False


class ConfirmationResult(BaseModel):
    """Confirmation prediction for one train + class + corridor."""

    train_number: str
    train_name: str | None = None
    origin: str
    destination: str
    date: str
    travel_class: str
    quota: str = "GN"
    confirm_chance: int | None = None
    confirm_status: str | None = None
    booking_status: str | None = None
    fare: int | None = None


class TrainSearchService:
    """Resolve a corridor, fetch trains, then filter/sort/limit (all deterministic)."""

    def __init__(
        self,
        train_provider: TrainProvider,
        station_service: StationService,
        *,
        cache: TTLCache[TrainSearchResult] | None = None,
        cache_ttl: float | None = None,
        default_limit: int = 20,
    ) -> None:
        self._provider = train_provider
        self._stations = station_service
        self._cache = cache
        self._cache_ttl = cache_ttl
        self._default_limit = default_limit

    async def _raw_search(self, src: str, dst: str, date: str) -> TrainSearchResult:
        key = f"search:{src}:{dst}:{date}"
        if self._cache is not None:
            return await self._cache.get_or_set(
                key, lambda: self._provider.search(src, dst, date), self._cache_ttl
            )
        return await self._provider.search(src, dst, date)

    async def raw_search_by_code(self, src: str, dst: str, date: str) -> TrainSearchResult:
        """Search using already-resolved station codes (used by the planner)."""
        return await self._raw_search(src.upper(), dst.upper(), date)

    async def search(
        self,
        origin: str,
        destination: str,
        date: str,
        *,
        filters: TrainFilters | None = None,
        sort_by: SortBy = SortBy.DEFAULT,
        preferred_train: str | None = None,
        limit: int | None = None,
        resolve: bool = True,
    ) -> SearchResponse:
        if resolve:
            src, dst = await asyncio.gather(
                self._stations.resolve_code(origin),
                self._stations.resolve_code(destination),
            )
        else:
            src, dst = origin.upper(), destination.upper()

        result = await self._raw_search(src, dst, date)
        filters = filters or TrainFilters()
        classes = filters.class_set

        matched = filter_trains(result.trains, filters)
        matched = sort_trains(matched, sort_by, classes=classes)
        matched = apply_preferred(matched, preferred_train)

        total = len(matched)
        lim = limit if limit is not None else self._default_limit
        if lim and lim > 0:
            matched = matched[:lim]

        return SearchResponse(
            origin=src,
            destination=dst,
            date=date,
            source_name=result.source_name,
            destination_name=result.destination_name,
            quotas=result.quotas,
            total_matched=total,
            trains=matched,
            alternatives=result.nearby_trains,
            from_cache=result.from_cache,
        )


class AvailabilityService:
    """Return one train's per-class availability on a corridor/date."""

    def __init__(self, search_service: TrainSearchService) -> None:
        self._search = search_service

    async def get_train(
        self, train_number: str, origin: str, destination: str, date: str
    ) -> Train:
        response = await self._search.search(
            origin, destination, date, sort_by=SortBy.DEFAULT, limit=0
        )
        num = train_number.strip()
        train = next((t for t in response.trains if t.number == num), None)
        if train is None:
            train = next((t for t in response.alternatives if t.number == num), None)
        if train is None:
            raise NoResultsError(
                f"Train {train_number} not found on {origin}->{destination} for {date}"
            )
        return train


class ConfirmationService:
    """Predict confirmation chance for a train + class via a pluggable estimator."""

    def __init__(
        self,
        availability_service: AvailabilityService,
        estimator: ConfirmationEstimator,
    ) -> None:
        self._availability = availability_service
        self._estimator = estimator

    async def predict(
        self,
        train_number: str,
        origin: str,
        destination: str,
        date: str,
        travel_class: str,
    ) -> ConfirmationResult:
        train = await self._availability.get_train(train_number, origin, destination, date)
        cls = travel_class.strip().upper()
        avail = next((a for a in train.availability if a.travel_class == cls), None)
        if avail is None:
            raise NoResultsError(
                f"Class {travel_class} not available on train {train_number}"
            )
        estimated: ClassAvailability = self._estimator.estimate(avail)
        return ConfirmationResult(
            train_number=train.number,
            train_name=train.name,
            origin=origin.upper(),
            destination=destination.upper(),
            date=date,
            travel_class=cls,
            quota=estimated.quota,
            confirm_chance=estimated.confirm_chance,
            confirm_status=estimated.confirm_status,
            booking_status=estimated.status_display or estimated.status,
            fare=estimated.fare,
        )
