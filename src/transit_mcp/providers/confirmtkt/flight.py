"""Flight search provider (reverse-engineered, endpoint-configurable).

Disabled by default (``TRANSIT_ENABLE_FLIGHT=false``). Mirrors the bus provider:
tolerant mapping, clean failure surfaced as an upstream error.
"""

from __future__ import annotations

from ...infra.errors import UpstreamError, UpstreamUnavailableError
from ...infra.http import HttpClient
from ...models.domain import Flight
from .mappers import map_flight


class ConfirmTktFlightProvider:
    """Implements :class:`~transit_mcp.interfaces.providers.FlightProvider`."""

    def __init__(
        self,
        http: HttpClient,
        *,
        path: str = "/api/v1/flights/search",
        results_key: str = "data",
    ) -> None:
        self._http = http
        self._path = path
        self._results_key = results_key

    async def search(self, src: str, dst: str, date: str) -> list[Flight]:
        payload = await self._http.get_json(
            self._path,
            {"origin": src, "destination": dst, "doj": date},
        )
        rows = self._extract_rows(payload)
        return [map_flight(r) for r in rows if isinstance(r, dict)]

    def _extract_rows(self, payload: object) -> list[dict]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get(self._results_key, payload)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("flightList", "flights", "results", "trips"):
                    if isinstance(data.get(key), list):
                        return data[key]
            raise UpstreamError("Unexpected flight payload shape", provider="flight")
        raise UpstreamUnavailableError("Empty flight payload", provider="flight")
