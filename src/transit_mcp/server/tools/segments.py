"""Booking-segment tool: a longer ticket on the same train that confirms more easily."""

from __future__ import annotations

from ...common import normalize_date
from ...models.envelopes import ok
from ...providers.estimators import PassthroughConfirmationEstimator
from ...services.segments import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_EXTRA_STATIONS,
    DEFAULT_MIN_GAIN_PCT,
    BookingSegmentService,
)
from ..app import Container
from ._guard import READ_ONLY, dump, tool_guard
from ._guidance import BOOKING_SEGMENT_GUIDANCE, segment_next_actions
from ._summaries import safe, summarize_segments


def register(mcp, c: Container) -> None:
    # Containers assembled by hand may not carry the service; build it from their parts.
    service = c.segments or BookingSegmentService(
        c.stations, c.train_search, c.routes, PassthroughConfirmationEstimator()
    )

    @mcp.tool(
        name="find_best_booking_segment",
        description=(
            "Find a LONGER booking on the SAME train that confirms more easily than your own "
            "origin->destination leg. Indian Railways quotas are per station pair, so a short "
            "leg can be waitlisted as RLWL/PQWL while the longer pair that starts at the "
            "train's origin is GNWL with better odds (e.g. DLI->MFP 3A RLWL 72% vs ASR->MFP "
            "GNWL 87%). You book the longer pair, change the boarding point to your origin in "
            "IRCTC, and get off at your destination.\n\n"
            "Inputs: origin/destination (codes or names), date (DD-MM-YYYY, YYYY-MM-DD, "
            "'today', 'tomorrow') = the day you board at origin. Optional: train_number (else "
            "the corridor's direct trains, up to 30, are analysed), travel_class (e.g. 3A; "
            "omit for every class - longer output), quota (only 'GN').\n\n"
            "Search: your origin and destination must be scheduled halts, in the train's "
            "direction (never a reverse trip). Candidate booking stations are the "
            "max_extra_stations_each_side (default 5) nearest halting major stops beyond each "
            "of your stations plus the train's origin and terminus. By default every longer "
            "pair in that window is looked up (max_candidates default 64, the max) - lower "
            "only to save cost. Ranking: pairs from the train's origin first, then "
            "boarding-side extensions, then alighting-side, then both, shortest extra "
            "distance first within each group. Only suggestions that beat your leg's "
            "confirm_chance by at least min_gain_pct points (default 10) are returned.\n\n"
            "Output: data.best plus data.trains[]; each train has user_leg (the baseline), "
            "suggestions sorted by gain then fare (booking_from/booking_to, departure_date and "
            "arrival_date, status, confirm_chance, extra_fare, extra_km, gain_pct, "
            "instruction), route_proof and warnings[]. A train with no suggestions carries a "
            "reason (e.g. 'not on route / wrong direction', 'user origin/destination is not a "
            "scheduled halt') and detail. departure_date is the real booking date and can be "
            "earlier than yours on overnight trains.\n\n"
            "Caveat: a boarding-point change is allowed once and only on a confirmed/RAC "
            "ticket, so this helps only if the waitlist clears. Pay for the whole booked "
            "segment; the unused leg is not refunded. Read warnings[]."
        ),
        annotations=READ_ONLY,
    )
    @tool_guard
    async def find_best_booking_segment(
        origin: str,
        destination: str,
        date: str,
        train_number: str | None = None,
        travel_class: str | None = None,
        quota: str | None = "GN",
        max_extra_stations_each_side: int = DEFAULT_MAX_EXTRA_STATIONS,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        min_gain_pct: int = DEFAULT_MIN_GAIN_PCT,
    ) -> dict:
        nd = normalize_date(date)
        report = await service.find(
            origin,
            destination,
            nd,
            train_number=train_number,
            travel_class=travel_class,
            quota=quota,
            max_extra_stations_each_side=max_extra_stations_each_side,
            max_candidates=max_candidates,
            min_gain_pct=min_gain_pct,
        )
        meta: dict = {
            "trains_analyzed": len(report.trains),
            "trains_matched": report.trains_matched,
            "suggestions": sum(len(t.suggestions) for t in report.trains),
            "date": nd,
            "guidance": BOOKING_SEGMENT_GUIDANCE,
        }
        summary = safe(summarize_segments, report)
        if summary:
            meta["summary"] = summary
        next_actions = segment_next_actions(report)
        if next_actions:
            meta["next_actions"] = next_actions
        return ok(dump(report), **meta)
