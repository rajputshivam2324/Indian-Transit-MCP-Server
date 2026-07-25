"""Station tools: code resolution and nearby/alternate suggestions."""

from __future__ import annotations

from ...models.envelopes import ok
from ..app import Container
from ._guard import READ_ONLY, dump, dump_all, tool_guard
from ._guidance import FIND_STATION_GUIDANCE


def register(mcp, c: Container) -> None:
    @mcp.tool(
        name="find_station_code",
        description=(
            "Resolve a city or station name to an IRCTC station code. Returns the "
            "best match plus a ranked list of candidate stations (code, name, city, "
            "state, whether it is a major station). Use this before searching when "
            "you only have a place name."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def find_station_code(query: str) -> dict:
        best, ranked = await c.stations.resolve(query)
        meta: dict = {"count": len(ranked)}
        if len(ranked) > 1:
            # More than one candidate: nudge the model to disambiguate, not guess.
            meta["guidance"] = FIND_STATION_GUIDANCE
        return ok(
            {
                "query": query,
                "resolved": dump(best),
                "suggestions": dump_all(ranked),
            },
            **meta,
        )

    @mcp.tool(
        name="suggest_nearby_stations",
        description=(
            "Given a place, suggest related stations: the resolved station, other "
            "stations in the same city, and nearby alternates. Each is tagged with a "
            "'source' (exact | city | nearby). Useful for finding alternate boarding "
            "or alighting points."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def suggest_nearby_stations(place: str) -> dict:
        result = await c.nearby.suggest(place)
        return ok(dump(result), count=len(result.stations))
