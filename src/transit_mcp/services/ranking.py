"""Pure ranking, filtering, and scoring helpers for train lists.

No I/O and no provider access, just deterministic transforms over domain models,
which makes them exhaustively unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..common import parse_time_to_minutes
from ..models.domain import ClassAvailability, Train
from ..models.enums import SortBy

_UNAVAILABLE_STATUSES = {
    "regret",
    "not available",
    "na",
    "cancelled",
    "train cancelled",
    "departed",
    "train departed",
}


def class_is_available(a: ClassAvailability) -> bool:
    """Whether a class can currently be booked (has seats, RAC, or a WL chance)."""
    status = (a.confirm_status or "").strip().lower()
    if status in _UNAVAILABLE_STATUSES:
        return False
    if a.seats and a.seats > 0:
        return True
    if a.confirm_chance is not None and a.confirm_chance > 0:
        return True
    disp = (a.status_display or "").upper()
    if disp.startswith(("AVL", "AVAILABLE", "RAC", "CURR")):
        return True
    return status in ("confirm", "confirmed", "available")


def _relevant(
    train: Train,
    classes: set[str] | None,
    quota: str | None,
) -> list[ClassAvailability]:
    out = train.availability
    if classes:
        out = [a for a in out if a.travel_class in classes]
    if quota:
        out = [a for a in out if a.quota == quota]
    return out


def min_fare(
    train: Train, classes: set[str] | None = None, quota: str | None = None
) -> int | None:
    fares = [a.fare for a in _relevant(train, classes, quota) if a.fare is not None]
    return min(fares) if fares else None


def best_confirm_chance(
    train: Train, classes: set[str] | None = None, quota: str | None = None
) -> int | None:
    chances = [
        a.confirm_chance
        for a in _relevant(train, classes, quota)
        if a.confirm_chance is not None
    ]
    return max(chances) if chances else None


def departure_minutes(train: Train) -> int:
    return parse_time_to_minutes(train.departure) or 0


def arrival_minutes_abs(train: Train) -> int:
    """Absolute arrival minute from journey start (departure + duration)."""
    if train.duration_min:
        return departure_minutes(train) + train.duration_min
    arr = parse_time_to_minutes(train.arrival)
    dep = departure_minutes(train)
    if arr is None:
        return dep
    return arr + (1440 if arr < dep else 0)


@dataclass(frozen=True)
class TrainFilters:
    """Tier-2 filter parameters. ``None`` fields are ignored."""

    classes: frozenset[str] = field(default_factory=frozenset)
    available_only: bool = False
    quota: str | None = None
    depart_after: str | None = None  # HH:MM
    depart_before: str | None = None  # HH:MM
    arrive_before: str | None = None  # HH:MM
    max_duration_min: int | None = None
    max_fare: int | None = None
    min_confirm_chance: int | None = None

    @property
    def class_set(self) -> set[str] | None:
        return set(self.classes) or None


def filter_trains(trains: list[Train], f: TrainFilters) -> list[Train]:
    """Return the subset of ``trains`` matching every active filter in ``f``."""
    classes = f.class_set
    out: list[Train] = []
    dep_after = parse_time_to_minutes(f.depart_after)
    dep_before = parse_time_to_minutes(f.depart_before)
    arr_before = parse_time_to_minutes(f.arrive_before)

    for t in trains:
        relevant = _relevant(t, classes, f.quota)
        if classes is not None and not relevant:
            continue
        if f.quota and not relevant:
            continue

        dep = departure_minutes(t)
        if dep_after is not None and dep < dep_after:
            continue
        if dep_before is not None and dep > dep_before:
            continue
        if arr_before is not None:
            arr = parse_time_to_minutes(t.arrival)
            if arr is not None and arr > arr_before:
                continue
        if f.max_duration_min is not None and t.duration_min > f.max_duration_min:
            continue

        pool = relevant if relevant else t.availability
        if f.available_only and not any(class_is_available(a) for a in pool):
            continue
        if f.max_fare is not None:
            mf = min_fare(t, classes, f.quota)
            if mf is None or mf > f.max_fare:
                continue
        if f.min_confirm_chance is not None:
            bc = best_confirm_chance(t, classes, f.quota)
            if bc is None or bc < f.min_confirm_chance:
                continue
        out.append(t)
    return out


def sort_trains(
    trains: list[Train],
    sort_by: SortBy = SortBy.DEFAULT,
    *,
    classes: set[str] | None = None,
) -> list[Train]:
    """Return a new list sorted per ``sort_by`` (stable; ``None`` values sort last)."""
    items = list(trains)
    big = float("inf")

    if sort_by == SortBy.DEPARTURE:
        items.sort(key=departure_minutes)
    elif sort_by == SortBy.ARRIVAL:
        items.sort(key=arrival_minutes_abs)
    elif sort_by == SortBy.DURATION:
        items.sort(key=lambda t: t.duration_min or big)
    elif sort_by == SortBy.PRICE:
        items.sort(key=lambda t: (min_fare(t, classes) if min_fare(t, classes) is not None else big))
    elif sort_by == SortBy.CONFIRMATION:
        items.sort(key=lambda t: -(best_confirm_chance(t, classes) or -1))
    elif sort_by == SortBy.DISTANCE:
        items.sort(key=lambda t: (t.distance if t.distance is not None else big))
    # SortBy.DEFAULT -> preserve upstream order
    return items


def apply_preferred(trains: list[Train], preferred: str | None) -> list[Train]:
    """Move a preferred train number to the front, preserving the rest of the order."""
    if not preferred:
        return trains
    pref = preferred.strip()
    head = [t for t in trains if t.number == pref]
    if not head:
        return trains
    tail = [t for t in trains if t.number != pref]
    return head + tail
