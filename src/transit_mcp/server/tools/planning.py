"""Advanced planning tools: split journeys, buses, flights, multi-modal trips."""

from __future__ import annotations

from ...common import normalize_date
from ...models.enums import TransportMode
from ...models.envelopes import ok
from ..app import Container
from ._guard import READ_ONLY, dump_all, tool_guard
from ._guidance import (
    PLAN_TRIP_GUIDANCE,
    SPLIT_JOURNEY_EMPTY_NOTE,
    SPLIT_JOURNEY_GUIDANCE,
)
from ._summaries import render_itinerary, safe, summarize_split, summarize_trip


def register(mcp, c: Container) -> None:
    @mcp.tool(
        name="plan_split_journey",
        description=(
            "Plan single-transfer train journeys when a good direct train is scarce. "
            "Derives hub stations from real routes between origin and destination, then "
            "searches both legs and returns itineraries ranked by combined confirmation "
            "chance (same-station transfers preferred), with layover, total duration, "
            "and total fare.\n\n"
            "Each itinerary reports the ACTUAL boarding/alighting station of every leg "
            "plus its departure_date and arrival_date (an overnight leg arrives on the "
            "next date), so verify these before trusting a transfer:\n"
            "- requires_station_change=true means leg 1 arrives and leg 2 departs at "
            "DIFFERENT stations in the same city (e.g. arrive BPL / depart RKMP); the "
            "layover does not include inter-station travel time.\n"
            "- warnings[] flags station changes, layovers too short to be realistic, a "
            "second leg that departs a calendar day after leg 1 ARRIVES, and any leg "
            "that is waitlisted (not 100% confirmed).\n"
            "Present the warnings to the user and prefer itineraries with none. Direct "
            "trains from search_trains are usually more reliable; use this as a fallback.\n\n"
            "Options: max_layover_hours, min_transfer_min, classes[], "
            "min_confirm_chance (0-100)."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def plan_split_journey(
        origin: str,
        destination: str,
        date: str,
        max_layover_hours: float | None = None,
        min_transfer_min: int | None = None,
        classes: list[str] | None = None,
        min_confirm_chance: int | None = None,
    ) -> dict:
        nd = normalize_date(date)
        class_set = {x.strip().upper() for x in (classes or []) if x.strip()} or None
        itineraries = await c.split.plan(
            origin,
            destination,
            nd,
            max_layover_hours=max_layover_hours,
            min_transfer_min=min_transfer_min,
            classes=class_set,
            min_confirm_chance=min_confirm_chance,
        )
        meta: dict = {
            "count": len(itineraries),
            "date": nd,
            "guidance": SPLIT_JOURNEY_GUIDANCE,
        }
        summary = safe(summarize_split, itineraries)
        if summary:
            meta["summary"] = summary
        if not itineraries:
            meta["note"] = SPLIT_JOURNEY_EMPTY_NOTE

        # Attach a complete, ready-to-present breakdown per itinerary (with the layover
        # explicitly labeled) so a weak model can echo it without dropping details.
        data = dump_all(itineraries)
        for row, it in zip(data, itineraries):
            display = safe(render_itinerary, it)
            if display:
                row["display"] = display
        return ok(data, **meta)

    @mcp.tool(
        name="search_buses",
        description=(
            "Search bus options between two places on a date. Buses are a "
            "feature-flagged data source; when unavailable this returns an empty list "
            "with meta.available=false and a reason (never an error)."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def search_buses(origin: str, destination: str, date: str) -> dict:
        nd = normalize_date(date)
        trips, meta = await c.multimodal.search_buses(origin, destination, nd)
        return ok(dump_all(trips), **meta)

    @mcp.tool(
        name="search_flights",
        description=(
            "Search flight options between two places on a date. Flights are a "
            "feature-flagged data source; when unavailable this returns an empty list "
            "with meta.available=false and a reason (never an error)."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def search_flights(origin: str, destination: str, date: str) -> dict:
        nd = normalize_date(date)
        flights, meta = await c.multimodal.search_flights(origin, destination, nd)
        return ok(dump_all(flights), **meta)

    @mcp.tool(
        name="plan_trip",
        description=(
            "Compare travel options across modes (train, bus, flight) as a single "
            "normalized, duration-sorted list of TripOptions (mode, summary, duration, "
            "price, confirmation chance, detail). Restrict with modes[] "
            "(e.g. ['train','flight']). Modes that are disabled/unavailable are "
            "reported in meta and simply omitted."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def plan_trip(
        origin: str,
        destination: str,
        date: str,
        modes: list[str] | None = None,
    ) -> dict:
        nd = normalize_date(date)
        mode_set: set[TransportMode] | None = None
        if modes:
            mode_set = set()
            for m in modes:
                try:
                    mode_set.add(TransportMode(m.strip().lower()))
                except ValueError:
                    continue
            mode_set = mode_set or None
        options, meta = await c.multimodal.plan_trip(origin, destination, nd, mode_set)
        out: dict = {"modes": meta, "count": len(options), "guidance": PLAN_TRIP_GUIDANCE}
        summary = safe(summarize_trip, options, meta)
        if summary:
            out["summary"] = summary
        return ok(dump_all(options), **out)
