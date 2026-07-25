"""Train tools: search (with rich filters) and single-train seat availability."""

from __future__ import annotations

from ...common import normalize_date
from ...models.enums import SortBy
from ...models.envelopes import ok
from ...services.ranking import TrainFilters
from ..app import Container
from ._guard import READ_ONLY, coerce_enum, dump, tool_guard
from ._guidance import SEARCH_TRAINS_GUIDANCE, search_next_actions
from ._summaries import safe, summarize_search


def register(mcp, c: Container) -> None:
    @mcp.tool(
        name="search_trains",
        description=(
            "Search direct trains between two stations on a date. Accepts station "
            "codes or names for origin/destination and a date (DD-MM-YYYY, YYYY-MM-DD, "
            "'today', or 'tomorrow').\n\n"
            "Sorting (sort_by): default | departure | arrival | duration | price | "
            "confirmation | distance.\n"
            "Filters: travel_class or classes[] (e.g. SL, 3A, 2A, 1A, CC, 2S), "
            "available_only, quota (GN/TQ/LD/SS), depart_after/depart_before/"
            "arrive_before (HH:MM), max_duration_min, max_fare, min_confirm_chance "
            "(0-100). preferred_train pins a train number to the top; limit caps "
            "results.\n\n"
            "Each train includes per-class availability with fare, waitlist status, "
            "and confirmation chance so you can explain trade-offs."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def search_trains(
        origin: str,
        destination: str,
        date: str,
        sort_by: str = "default",
        travel_class: str | None = None,
        classes: list[str] | None = None,
        available_only: bool = False,
        quota: str | None = None,
        depart_after: str | None = None,
        depart_before: str | None = None,
        arrive_before: str | None = None,
        max_duration_min: int | None = None,
        max_fare: int | None = None,
        min_confirm_chance: int | None = None,
        preferred_train: str | None = None,
        limit: int | None = None,
    ) -> dict:
        nd = normalize_date(date)
        class_set = {c.strip().upper() for c in (classes or []) if c.strip()}
        if travel_class and travel_class.strip():
            class_set.add(travel_class.strip().upper())

        filters = TrainFilters(
            classes=frozenset(class_set),
            available_only=available_only,
            quota=quota.strip().upper() if quota else None,
            depart_after=depart_after,
            depart_before=depart_before,
            arrive_before=arrive_before,
            max_duration_min=max_duration_min,
            max_fare=max_fare,
            min_confirm_chance=min_confirm_chance,
        )
        resp = await c.train_search.search(
            origin,
            destination,
            nd,
            filters=filters,
            sort_by=coerce_enum(SortBy, sort_by, SortBy.DEFAULT),
            preferred_train=preferred_train,
            limit=limit,
        )
        meta: dict = {
            "returned": len(resp.trains),
            "total_matched": resp.total_matched,
            "from_cache": resp.from_cache,
            "guidance": SEARCH_TRAINS_GUIDANCE,
        }
        summary = safe(summarize_search, resp)
        if summary:
            meta["summary"] = summary
        next_actions = search_next_actions(resp)
        if next_actions:
            meta["next_actions"] = next_actions
        return ok(dump(resp), **meta)

    @mcp.tool(
        name="get_seat_availability",
        description=(
            "Get per-class seat availability, fare, and confirmation prediction for a "
            "single train on a corridor and date. Provide the train number plus origin/"
            "destination (codes or names) and date."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def get_seat_availability(
        train_number: str, origin: str, destination: str, date: str
    ) -> dict:
        nd = normalize_date(date)
        train = await c.availability.get_train(train_number, origin, destination, nd)
        return ok(
            dump(train),
            classes=len(train.availability),
            date=nd,
        )
