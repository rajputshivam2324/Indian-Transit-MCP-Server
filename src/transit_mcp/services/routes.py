"""Route lookup and nearby/alternate station suggestions."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..infra.cache import TTLCache
from ..interfaces.providers import RouteProvider
from ..models.domain import Station, TrainRoute
from .stations import StationService, score_station


class NearbyStation(BaseModel):
    code: str
    name: str
    city: str | None = None
    state: str | None = None
    is_major: bool = False
    source: str = "nearby"  # "exact" | "city" | "nearby"


class NearbyStationsResult(BaseModel):
    query: str
    matched_code: str
    matched_name: str
    stations: list[NearbyStation] = Field(default_factory=list)


class RouteService:
    """Fetch full ordered routes, cached by train number."""

    def __init__(
        self,
        provider: RouteProvider,
        *,
        cache: TTLCache[TrainRoute] | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._cache_ttl = cache_ttl

    async def get_route(self, train_number: str, date: str | None = None) -> TrainRoute:
        num = str(train_number).strip()
        key = f"route:{num}"
        if self._cache is not None:
            return await self._cache.get_or_set(
                key, lambda: self._provider.route(num, date), self._cache_ttl
            )
        return await self._provider.route(num, date)


class NearbyStationService:
    """Cluster autosuggest results into same-city and nearby alternates."""

    def __init__(self, station_service: StationService) -> None:
        self._stations = station_service

    async def suggest(self, place: str) -> NearbyStationsResult:
        matched, ranked = await self._stations.resolve(place)
        match_city = (matched.city or "").lower()

        def source_for(s: Station) -> str:
            if s.code == matched.code:
                return "exact"
            if match_city and (s.city or "").lower() == match_city:
                return "city"
            return "nearby"

        stations = [
            NearbyStation(
                code=s.code,
                name=s.name,
                city=s.city,
                state=s.state,
                is_major=s.is_major,
                source=source_for(s),
            )
            for s in ranked
        ]
        return NearbyStationsResult(
            query=place,
            matched_code=matched.code,
            matched_name=matched.name,
            stations=stations,
        )
