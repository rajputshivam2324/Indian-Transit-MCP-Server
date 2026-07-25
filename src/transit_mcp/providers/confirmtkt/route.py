"""ConfirmTkt train schedule/route provider."""

from __future__ import annotations

from ...infra.errors import NoResultsError
from ...infra.http import HttpClient
from ...models.domain import TrainRoute
from .mappers import map_route

_PATH = "/api/v1/trains/schedule"


class ConfirmTktRouteProvider:
    """Implements :class:`~transit_mcp.interfaces.providers.RouteProvider`."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    async def route(self, train_number: str, date: str | None = None) -> TrainRoute:
        params: dict[str, object] = {"trainNo": str(train_number).strip()}
        if date:
            params["doj"] = date
        payload = await self._http.get_json(_PATH, params)
        route = map_route(payload or {})
        if not route.stops:
            msg = (payload or {}).get("ErrorMsg") or "No schedule found"
            raise NoResultsError(
                f"No route available for train {train_number}: {msg}",
                provider="confirmtkt",
            )
        return route
