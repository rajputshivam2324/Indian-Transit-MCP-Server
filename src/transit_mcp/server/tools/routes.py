"""Route and confirmation-prediction tools."""

from __future__ import annotations

from ...common import normalize_date
from ...models.envelopes import ok
from ..app import Container
from ._guard import READ_ONLY, dump, tool_guard
from ._guidance import PREDICT_CONFIRMATION_GUIDANCE
from ._summaries import safe, summarize_confirmation


def register(mcp, c: Container) -> None:
    @mcp.tool(
        name="get_train_route",
        description=(
            "Get the ordered route for a train: each stop with arrival/departure times, "
            "day of journey, halt, distance from origin, and platform (when known). "
            "Major stops are flagged.\n\n"
            "stops='all' (default) returns every stop - complete but large (a long-"
            "distance train can have 200+ stops). stops='major' returns only junctions/"
            "major stops, a compact view for a quick overview or a small context window; "
            "meta.total_stops always reports the full count. Optionally pass a date."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def get_train_route(
        train_number: str, date: str | None = None, stops: str = "all"
    ) -> dict:
        nd = normalize_date(date) if date else None
        route = await c.routes.get_route(train_number, nd)
        data = dump(route)
        total = len(route.stops)
        meta: dict = {"total_stops": total}
        if str(stops).strip().lower() == "major":
            data["stops"] = [s for s in data.get("stops", []) if s.get("is_major")]
            meta["stops_view"] = "major"
            meta["shown_stops"] = len(data["stops"])
            meta["note"] = (
                f"Showing {len(data['stops'])} major stops of {total} total; "
                "call again with stops='all' for every stop."
            )
        else:
            meta["shown_stops"] = total
        return ok(data, **meta)

    @mcp.tool(
        name="predict_confirmation",
        description=(
            "Predict the confirmation chance for a specific train, class, and corridor "
            "on a date. Returns the confirmation percentage, booking status (e.g. "
            "waitlist position), and fare."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def predict_confirmation(
        train_number: str,
        origin: str,
        destination: str,
        date: str,
        travel_class: str,
    ) -> dict:
        nd = normalize_date(date)
        result = await c.confirmation.predict(
            train_number, origin, destination, nd, travel_class
        )
        meta: dict = {"guidance": PREDICT_CONFIRMATION_GUIDANCE}
        summary = safe(summarize_confirmation, result)
        if summary:
            meta["summary"] = summary
        return ok(dump(result), **meta)
