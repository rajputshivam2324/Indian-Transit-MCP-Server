"""Domain models returned by services and tools (pydantic v2).

These are the stable, provider-agnostic representations. Mappers in the provider
layer are responsible for translating raw upstream payloads into these shapes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .enums import TransportMode

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class Station(BaseModel):
    """A railway station (or a city-level "all stations" grouping)."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    city: str | None = None
    state: str | None = None
    is_major: bool = False
    airport_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ClassAvailability(BaseModel):
    """Availability, fare and confirmation prediction for one travel class."""

    travel_class: str
    quota: str = "GN"
    status: str | None = None  # raw, e.g. "GNWL38/WL10" or "AVAILABLE-0021"
    status_display: str | None = None  # friendly, e.g. "WL 10" / "AVL 21"
    seats: int | None = None  # parsed count when the status exposes one
    fare: int | None = None  # in INR
    confirm_status: str | None = None  # e.g. "Confirm", "Probable", "Regret"
    confirm_chance: int | None = None  # 0-100, when predicted


class Train(BaseModel):
    """A direct train option between an origin and destination on a date."""

    number: str
    name: str
    from_code: str
    from_name: str | None = None
    to_code: str
    to_name: str | None = None
    departure: str  # HH:MM
    arrival: str  # HH:MM
    departure_date: str | None = None  # DD-MM-YYYY
    duration_min: int = 0
    distance: int | None = None  # km
    train_type: str | None = None
    has_pantry: bool = False
    rating: float | None = None
    running_days_mask: str | None = None  # 7 chars, Mon..Sun, "1" = runs
    availability: list[ClassAvailability] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_fmt(self) -> str:
        h, m = divmod(max(self.duration_min, 0), 60)
        return f"{h}h {m:02d}m"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def running_days(self) -> list[str]:
        mask = self.running_days_mask or ""
        return [day for day, flag in zip(_WEEKDAYS, mask) if flag == "1"]


class TrainSearchResult(BaseModel):
    """Everything a single upstream train search yields for a corridor/date."""

    source_code: str
    destination_code: str
    date: str  # DD-MM-YYYY
    source_name: str | None = None
    destination_name: str | None = None
    quotas: list[str] = Field(default_factory=list)
    trains: list["Train"] = Field(default_factory=list)
    nearby_trains: list["Train"] = Field(default_factory=list)
    from_cache: bool = False


class Stop(BaseModel):
    """A single stop on a train's route/schedule."""

    code: str
    name: str
    arrival: str | None = None  # HH:MM, None at origin
    departure: str | None = None  # HH:MM, None at destination
    day: int = 1  # day of journey (1-based)
    halt_min: int | None = None
    distance_from_origin: float | None = None  # km
    platform: str | None = None
    is_major: bool = False


class TrainRoute(BaseModel):
    """Full ordered route for a train."""

    number: str
    name: str
    from_code: str
    to_code: str
    train_type: str | None = None
    total_duration_min: int | None = None
    has_pantry: bool = False
    stops: list[Stop] = Field(default_factory=list)


class JourneyLeg(BaseModel):
    """One train leg within a split itinerary.

    ``from_code``/``to_code`` are the leg's *actual* boarding/alighting stations
    (which may differ from the requested corridor when the upstream "nearby"
    search substitutes a variant station), so the client can reason about the
    real transfer.
    """

    train: Train
    from_code: str
    to_code: str
    departure_date: str | None = None  # DD-MM-YYYY (leg 2 may be the next day)
    availability: ClassAvailability | None = None


class SplitItinerary(BaseModel):
    """A single-transfer itinerary combining two train legs.

    ``transfer_station`` is the hub city anchor. Because the two legs can use
    *different stations in the same city* (e.g. arrive Bhopal Jn / depart Rani
    Kamlapati), the actual alighting and boarding stations are exposed separately
    and ``requires_station_change`` flags when they differ. ``warnings`` carries
    human-readable feasibility notes (station change, tight layover, next-day
    leg, waitlisted legs) so the caller does not have to re-derive them.
    """

    legs: list[JourneyLeg]
    transfer_station: str
    transfer_arrival_station: str | None = None  # where leg 1 actually arrives
    transfer_departure_station: str | None = None  # where leg 2 actually departs
    requires_station_change: bool = False
    travel_class: str | None = None  # class the fare/chance are for (shared by both legs)
    total_duration_min: int
    layover_min: int
    total_fare: int | None = None  # sum of BOTH legs' fare for `travel_class`
    combined_confirm_chance: float | None = None  # product of leg chances, 0-100
    warnings: list[str] = Field(default_factory=list)


class BusTrip(BaseModel):
    """A bus option (reverse-engineered / partner data)."""

    operator: str
    bus_type: str | None = None
    departure: str | None = None
    arrival: str | None = None
    duration_min: int | None = None
    fare: int | None = None
    seats_available: int | None = None


class Flight(BaseModel):
    """A flight option (reverse-engineered / partner data)."""

    airline: str
    flight_number: str | None = None
    departure: str | None = None
    arrival: str | None = None
    duration_min: int | None = None
    stops: int = 0
    fare: int | None = None


class TripOption(BaseModel):
    """A normalized row for multi-modal comparison across trains/buses/flights."""

    mode: TransportMode
    summary: str
    total_duration_min: int | None = None
    price: int | None = None
    confirm_chance: float | None = None
    detail: dict = Field(default_factory=dict)
