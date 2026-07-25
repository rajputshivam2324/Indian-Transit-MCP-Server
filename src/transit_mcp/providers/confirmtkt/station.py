"""ConfirmTkt station auto-suggestion provider."""

from __future__ import annotations

from ...infra.http import HttpClient
from ...models.domain import Station
from .mappers import map_stations

_PATH = "/api/v2/trains/stations/auto-suggestion"


class ConfirmTktStationProvider:
    """Implements :class:`~transit_mcp.interfaces.providers.StationProvider`."""

    def __init__(
        self,
        http: HttpClient,
        *,
        popular_limit: int = 15,
        preferred_limit: int = 6,
    ) -> None:
        self._http = http
        self._popular_limit = popular_limit
        self._preferred_limit = preferred_limit

    async def autosuggest(self, query: str) -> list[Station]:
        payload = await self._http.get_json(
            _PATH,
            {
                "searchString": query,
                "sourceStnCode": "",
                "popularStnListLimit": self._popular_limit,
                "preferredStnListLimit": self._preferred_limit,
                "channel": "mwebd",
                "language": "EN",
            },
        )
        return map_stations(payload)
