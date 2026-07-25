"""ConfirmTkt train search provider."""

from __future__ import annotations

from ...infra.http import HttpClient
from ...infra.logging import get_logger
from ...models.domain import TrainSearchResult
from .mappers import map_search_result

log = get_logger("provider.train")

_PATH = "/api/v1/trains/search"


class ConfirmTktTrainProvider:
    """Implements :class:`~transit_mcp.interfaces.providers.TrainProvider`."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    async def search(self, src: str, dst: str, date: str) -> TrainSearchResult:
        payload = await self._http.get_json(
            _PATH,
            {
                "sourceStationCode": src.upper(),
                "destinationStationCode": dst.upper(),
                "dateOfJourney": date,
                "addAvailabilityCache": "true",
                "sortBy": "DEFAULT",
                "enableNearby": "true",
                "enableTG": "true",
                "showPredictionGlobal": "true",
            },
        )
        data = (payload or {}).get("data") or {}
        if data.get("errorMessage"):
            log.info(
                "search %s->%s returned errorMessage=%r errorCode=%r",
                src,
                dst,
                data.get("errorMessage"),
                data.get("errorCode"),
            )
        return map_search_result(payload, src=src, dst=dst, date=date)
