"""Result models for ``find_best_booking_segment`` (pydantic v2).

A *booking segment* is a longer ``booking_from -> booking_to`` ticket on the same train
that fully covers the traveller's own ``user_from -> user_to`` leg. Indian Railways quotas
are per station pair, so the longer pair is sometimes far easier to confirm (for example
RLWL / PQWL on the short pair versus GNWL on the pair that starts at the train's origin).
The traveller books the longer pair, moves their boarding point to ``user_from`` and gets
off at ``user_to``.

These models carry everything a client needs to explain that trade-off. Tools dump them
with ``exclude_none`` so optional fields that do not apply are simply absent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SegmentClassFare(BaseModel):
    """Baseline (``user_leg``) availability for one travel class."""

    travel_class: str
    quota: str = "GN"
    fare: int | None = None  # INR
    status: str | None = None  # raw, e.g. "RLWL21/WL13" - the prefix is the waitlist type
    confirm_chance: int | None = None  # 0-100


class SegmentUserLeg(BaseModel):
    """The traveller's own leg on one train, used as the comparison baseline.

    ``travel_class`` / ``fare`` / ``status`` / ``confirm_chance`` describe a single class and
    are only set when the caller asked for a class (or the train offers just one). With
    several classes and no filter, read ``classes`` instead - picking one would be arbitrary.
    """

    from_code: str
    to_code: str
    from_name: str | None = None
    to_name: str | None = None
    departure: str | None = None  # HH:MM at from_code
    arrival: str | None = None  # HH:MM at to_code
    departure_date: str | None = None  # DD-MM-YYYY
    arrival_date: str | None = None  # DD-MM-YYYY, later than departure_date when overnight
    distance: int | None = None  # km
    travel_class: str | None = None
    quota: str = "GN"
    fare: int | None = None
    status: str | None = None
    confirm_chance: int | None = None
    classes: list[SegmentClassFare] = Field(default_factory=list)


class SegmentSuggestion(BaseModel):
    """One longer booking that beats the user leg for one travel class."""

    booking_from: str
    booking_to: str
    departure: str | None = None  # HH:MM at booking_from
    arrival: str | None = None  # HH:MM at booking_to
    # Dates differ from the requested date whenever booking_from is on an earlier day of
    # the train's run than user_from (overnight trains). Search/book with departure_date.
    departure_date: str | None = None  # DD-MM-YYYY
    arrival_date: str | None = None  # DD-MM-YYYY
    travel_class: str
    quota: str = "GN"
    fare: int | None = None
    status: str | None = None  # raw, e.g. "GNWL34/WL19"
    confirm_chance: int  # 0-100
    baseline_status: str | None = None
    baseline_confirm_chance: int  # 0-100, same class on the user leg
    extra_fare: int | None = None  # fare minus the same class's fare on the user leg
    extra_km: int | None = None  # km booked beyond the user leg (both sides combined)
    gain_pct: int  # confirm_chance - baseline_confirm_chance, in percentage points
    instruction: str


class SegmentStop(BaseModel):
    """One station in ``route_proof``: proves the stations lie on the route, in order."""

    code: str
    name: str | None = None
    distance_km: float | None = None  # from the train's origin
    # user_origin | user_destination | booking_from | booking_to | train_origin | train_terminus
    role: str
    halt_min: int | None = None  # scheduled halt; None at the origin/terminus


class TrainSegmentResult(BaseModel):
    """The analysis of one train: baseline, suggestions, and the proof behind them.

    When a train cannot be analysed (or nothing beats the baseline) ``suggestions`` is
    empty and ``reason`` says why: a short stable string, with ``detail`` carrying the
    specifics in a sentence.
    """

    train_number: str
    train_name: str | None = None
    user_leg: SegmentUserLeg | None = None
    suggestions: list[SegmentSuggestion] = Field(default_factory=list)
    route_proof: list[SegmentStop] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reason: str | None = None
    detail: str | None = None
    # Transparency about how much of the search space was actually tried.
    candidates_considered: int = 0  # extended (booking_from, booking_to) pairs that exist
    candidates_evaluated: int = 0  # pairs looked up (capped by max_candidates)
    candidates_unavailable: int = 0  # looked up but the train/class had no usable data


class BestSegment(BaseModel):
    """The single best suggestion across all trains (highest gain, then lowest fare)."""

    train_number: str
    train_name: str | None = None
    suggestion: SegmentSuggestion


class BookingSegmentReport(BaseModel):
    """Final, client-facing result of ``find_best_booking_segment``."""

    origin: str  # resolved user origin code
    destination: str  # resolved user destination code
    date: str  # DD-MM-YYYY: the day the traveller boards at `origin`
    train_number: str | None = None  # set when the caller pinned a single train
    quota: str = "GN"
    travel_class: str | None = None
    min_gain_pct: int
    max_extra_stations_each_side: int
    max_candidates: int
    trains_matched: int = 0  # trains found for the corridor (before the analysis limit)
    trains: list[TrainSegmentResult] = Field(default_factory=list)
    best: BestSegment | None = None
    reason: str | None = None  # set when no train produced a suggestion
