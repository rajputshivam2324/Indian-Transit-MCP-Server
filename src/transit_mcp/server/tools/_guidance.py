"""Reasoning scaffolds (chain-of-thought + ReAct) embedded in tool results.

These ride in each tool's ``meta`` so client models - especially smaller ones -
follow a complete reasoning procedure and chain follow-up tools instead of
stopping early. The goal is comprehensive answers where nothing is dropped.

- ``*_GUIDANCE`` are full CoT step lists for a specific tool.
- ``*_next_actions`` compute ReAct-style follow-ups from the actual result, so the
  model knows which tool to call next when the answer is incomplete.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Full chain-of-thought scaffolds
# --------------------------------------------------------------------------- #
SEARCH_TRAINS_GUIDANCE = [
    "Present EVERY train in data.trains (up to the limit) - do not silently drop any.",
    "For each train, list every class in availability with its status, fare, and "
    "confirm_chance; distinguish AVAILABLE / RAC / WL and call out Tatkal (quota TQ) "
    "separately from General.",
    "Compare trade-offs explicitly: fastest vs cheapest vs highest confirmation.",
    "State meta.total_matched vs the number returned; if more exist, offer to show them.",
    "If the user gave no class or quota, summarize across classes instead of assuming one.",
    "When results are thin or waitlisted, follow meta.next_actions before concluding.",
]

PLAN_TRIP_GUIDANCE = [
    "Weigh options on duration, price, AND confirmation together - not a single axis.",
    "Tell the user which modes were unavailable (see meta.modes) so the comparison is "
    "not mistaken for complete.",
    "A waitlisted train row is not a confirmed seat - say so when you cite it.",
    "Recommend one option and justify it against the alternatives.",
]

PREDICT_CONFIRMATION_GUIDANCE = [
    "Read confirm_chance together with booking_status (e.g. 'WL 7' vs 'RAC 2' vs 'AVL 40').",
    "State the quota - Tatkal and General odds differ for the same train/class.",
    "Be honest about uncertainty: a prediction is not a guarantee. If the chance is low, "
    "suggest a backup (another train, class, date, or plan_split_journey).",
]

FIND_STATION_GUIDANCE = [
    "Several stations matched. If more than one is plausible (different cities or "
    "states), confirm with the user before searching rather than silently picking the top.",
    "Prefer is_major stations for city-level queries, and mention the alternates.",
]

SPLIT_JOURNEY_GUIDANCE = [
    "Step 1 - Prefer a confirmed direct train (search_trains) if one exists; treat "
    "these single-transfer options as a fallback.",
    "Step 2 - For each option, read warnings[] first: they flag the blockers you must "
    "not overlook.",
    "Step 3 - Check requires_station_change. If true, leg 1 arrives and leg 2 departs "
    "at DIFFERENT stations in the same city, and layover_min does NOT include time to "
    "travel between them - treat short layovers as unrealistic.",
    "Step 4 - A journey is only as reliable as its weakest leg: inspect each leg's "
    "availability.confirm_chance and status, not just combined_confirm_chance.",
    "Step 5 - travel_class/total_fare describe one class booked on both legs; read each "
    "leg's departure_date AND arrival_date. An overnight leg 1 arrives on the following "
    "date, so compare leg 2's departure_date against leg 1's arrival_date - not against "
    "leg 1's departure_date - before describing a connection as a day-long wait.",
    "Step 6 - Recommend the highest combined_confirm_chance option with no blocking "
    "warnings, explain the trade-offs of the rest, and surface every warning to the user.",
    "Step 7 - Each itinerary has a ready-to-present `display` string containing both "
    "legs' times, class, fare, and confirmation, the LAYOVER at the transfer, and the "
    "totals. Present every field in `display` for each option you show - never omit the "
    "layover or a leg's confirmation.",
]

SPLIT_JOURNEY_EMPTY_NOTE = (
    "No viable single-transfer itineraries were found. Prefer a direct train "
    "(search_trains), or retry with a larger max_layover_hours or a lower "
    "min_confirm_chance."
)


# --------------------------------------------------------------------------- #
# ReAct-style next actions (grounded in the actual result)
# --------------------------------------------------------------------------- #
def _has_open_class(train) -> bool:
    """True if the train has a genuinely open class (AVL/RAC/seats), not just a
    waitlist with some predicted chance."""
    for a in train.availability:
        if a.seats and a.seats > 0:
            return True
        if (a.status_display or "").upper().startswith(("AVL", "AVAILABLE", "RAC")):
            return True
        if (a.confirm_status or "").strip().lower() in ("confirm", "confirmed", "available"):
            return True
    return False


def search_next_actions(resp) -> list[str]:
    """Follow-up tool suggestions when a train search is empty/waitlisted/truncated."""
    if not resp.trains:
        return [
            "No direct trains matched. Verify the station codes with find_station_code.",
            "Try suggest_nearby_stations for alternate boarding/alighting points.",
            "Retry with another date, or relax filters (available_only, max_fare, "
            "min_confirm_chance).",
            "Use plan_split_journey for single-transfer options, or plan_trip to compare "
            "buses and flights.",
        ]

    actions: list[str] = []
    if not any(_has_open_class(t) for t in resp.trains):
        actions.append(
            "No shown train has an open (AVL/RAC) seat - all are waitlisted. Call "
            "predict_confirmation on the highest-chance train/class before advising a booking."
        )
        actions.append(
            "Offer plan_split_journey (single transfer) or suggest_nearby_stations as "
            "alternatives, or try another date."
        )
    extra = resp.total_matched - len(resp.trains)
    if extra > 0:
        actions.append(f"{extra} more matches exist beyond the limit; raise `limit` to see them.")
    return actions
