"""Bus search provider (reverse-engineered, endpoint-configurable).

ConfirmTkt's bus endpoint is not publicly stable, so this provider is disabled by
default (``TRANSIT_ENABLE_BUS=false``). When enabled and pointed at a working
endpoint, it maps results defensively; any failure surfaces as an upstream error
that the service turns into a clean "unavailable" response.
"""

from __future__ import annotations

from ...infra.errors import UpstreamError, UpstreamUnavailableError
from ...infra.http import HttpClient
from ...models.domain import BusTrip
from .mappers import map_bus


class ConfirmTktBusProvider:
    """Implements :class:`~transit_mcp.interfaces.providers.BusProvider`."""

    def __init__(
        self,
        http: HttpClient,
        *,
        path: str = "/api/v1/bus/search",
        results_key: str = "data",
    ) -> None:
        self._http = http
        self._path = path
        self._results_key = results_key

    async def search(self, src: str, dst: str, date: str) -> list[BusTrip]:
        payload = await self._http.get_json(
            self._path,
            {"sourceCity": src, "destinationCity": dst, "doj": date},
        )
        rows = self._extract_rows(payload)
        return [map_bus(r) for r in rows if isinstance(r, dict)]

    def _extract_rows(self, payload: object) -> list[dict]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get(self._results_key, payload)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("busList", "buses", "trips", "results"):
                    if isinstance(data.get(key), list):
                        return data[key]
            raise UpstreamError("Unexpected bus payload shape", provider="bus")
        raise UpstreamUnavailableError("Empty bus payload", provider="bus")
