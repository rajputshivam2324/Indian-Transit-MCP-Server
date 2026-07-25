"""Translate raw ConfirmTkt JSON payloads into domain models.

Every mapper is tolerant: missing or renamed fields degrade to ``None``/defaults
rather than raising, so additive upstream changes don't break the server.
"""

from __future__ import annotations

import re

from ...common import (
    extract_percentage,
    parse_duration_to_minutes,
    parse_int,
)
from ...interfaces.raw import (
    AvailabilityRaw,
    ScheduleRaw,
    StationRaw,
    StopRaw,
    TrainRaw,
)
from ...models.domain import (
    ClassAvailability,
    Station,
    Stop,
    Train,
    TrainRoute,
    TrainSearchResult,
)

_AVAILABLE_HINT = re.compile(r"(AVL|AVAILABLE|CURR_AVBL|RAC)", re.IGNORECASE)

# Upstream terminates availability/booking statuses with a '#' and caps the value at 14
# characters, so observed payloads contain "NOT AVAILABLE#" and "AVAILABLE-0060#", and
# anything longer is cut mid-word ("TRAIN CANCELLED" -> "TRAIN CANCELLE#"). Repair that
# here, at the provider boundary: clients display these strings verbatim and
# `services.ranking` matches them against an exact vocabulary ("train cancelled"), which
# a terminated or clipped value silently misses.
_STATUS_PAD_RE = re.compile(r"[#\s]+$")
_STATUS_HEAD_RE = re.compile(r"^[A-Za-z][A-Za-z _]*")
_MIN_REPAIR_PREFIX = 4  # never complete a stub too short to identify

# Deliberately free of prefix-overlapping entries, so a truncated head either matches
# exactly one canonical status or is left untouched.
_STATUS_VOCABULARY = (
    "AVAILABLE",
    "NOT AVAILABLE",
    "CURR_AVBL",
    "RAC",
    "WL",
    "GNWL",
    "PQWL",
    "RLWL",
    "TQWL",
    "CKWL",
    "REGRET",
    "CONFIRM",
    "CONFIRMED",
    "PROBABLE",
    "TRAIN CANCELLED",
    "TRAIN DEPARTED",
    "DEPARTED",
    "CHART PREPARED",
    "CHART NOT PREPARED",
    "BOOKING CLOSED",
    "RELEASED",
)


def _apply_case(sample: str, canonical: str) -> str:
    """Render ``canonical`` in the same case style as ``sample``."""
    if sample.isupper():
        return canonical
    if sample.islower():
        return canonical.lower()
    return canonical.title()


def sanitize_status(value: object) -> str | None:
    """Clean one raw upstream status string.

    Drops the trailing ``#`` terminator and completes a word the 14-character cap cut
    short, but only when the stub matches exactly one known status. Anything
    unrecognized is returned as-is (minus the terminator) - an unfamiliar status is
    still useful to the caller, an invented one is not.
    """
    text = _blank_to_none(value)
    if text is None:
        return None
    text = _STATUS_PAD_RE.sub("", text).strip()
    if not text:
        return None

    match = _STATUS_HEAD_RE.match(text)
    if match is None:
        return text
    head = match.group().rstrip()
    if not head or head.upper() in _STATUS_VOCABULARY:
        return text  # already a complete status; keep any numeric tail intact
    if len(head) < _MIN_REPAIR_PREFIX:
        return text
    candidates = [c for c in _STATUS_VOCABULARY if c.startswith(head.upper())]
    if len(candidates) != 1:
        return text  # ambiguous ("TRAIN " -> CANCELLED or DEPARTED); do not guess
    return _apply_case(head, candidates[0]) + text[match.end() :]


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _blank_to_none(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# --------------------------------------------------------------------------- #
# Stations
# --------------------------------------------------------------------------- #
def map_station(raw: StationRaw) -> Station:
    return Station(
        code=str(raw.get("stationCode") or raw.get("code") or "").strip().upper(),
        name=str(raw.get("stationName") or raw.get("name") or "").strip(),
        city=_blank_to_none(raw.get("city")),
        state=_blank_to_none(raw.get("state")),
        is_major=bool(raw.get("majorStn", False)),
        airport_code=_blank_to_none(raw.get("airportCode")),
        latitude=_to_float(raw.get("latitude")),
        longitude=_to_float(raw.get("longitude")),
    )


def map_stations(data: dict) -> list[Station]:
    station_list = ((data or {}).get("data") or {}).get("stationList") or []
    out: list[Station] = []
    for raw in station_list:
        station = map_station(raw)
        if station.code:
            out.append(station)
    return out


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
def _extract_seats(status_raw: str | None, display: str | None) -> int | None:
    for text in (display, status_raw):
        if text and _AVAILABLE_HINT.search(text):
            m = re.search(r"(\d+)", text)
            if m:
                return int(m.group(1))
    return None


def map_availability(raw: AvailabilityRaw) -> ClassAvailability:
    status_raw = sanitize_status(raw.get("availability"))
    display = sanitize_status(raw.get("availabilityDisplayName"))
    chance = extract_percentage(raw.get("predictionPercentage"))
    if chance is None:
        chance = extract_percentage(raw.get("prediction"))
    return ClassAvailability(
        travel_class=str(raw.get("travelClass") or "").strip().upper(),
        quota=str(raw.get("quota") or "GN").strip().upper(),
        status=status_raw,
        status_display=display,
        seats=_extract_seats(status_raw, display),
        fare=parse_int(raw.get("fare")),
        confirm_status=sanitize_status(raw.get("confirmTktStatus")),
        confirm_chance=chance,
    )


def _availability_list(raw: TrainRaw) -> list[ClassAvailability]:
    general = raw.get("availabilityCache") or {}
    tatkal = raw.get("availabilityCacheTatkal") or {}
    ordered = raw.get("avlClassesSorted") or []

    out: list[ClassAvailability] = []
    seen: set[str] = set()

    def add(key: str, source: dict) -> None:
        if key in seen:
            return
        entry = source.get(key)
        if isinstance(entry, dict):
            out.append(map_availability(entry))
            seen.add(key)

    # Preferred order (interleaves general + _TQ tatkal classes).
    for cls in ordered:
        if cls.endswith("_TQ"):
            add(cls, {cls: tatkal.get(cls[:-3])})
        else:
            add(cls, {cls: general.get(cls)})

    # Fallback: anything not covered by the sorted list.
    for cls, entry in general.items():
        if cls not in seen and isinstance(entry, dict):
            out.append(map_availability(entry))
            seen.add(cls)
    return out


# --------------------------------------------------------------------------- #
# Trains
# --------------------------------------------------------------------------- #
def map_train(raw: TrainRaw, *, default_date: str | None = None) -> Train:
    duration = parse_int(raw.get("duration"))
    return Train(
        number=str(raw.get("trainNumber") or "").strip(),
        name=str(raw.get("trainName") or "").strip(),
        from_code=str(raw.get("fromStnCode") or "").strip().upper(),
        from_name=_blank_to_none(raw.get("fromStnName")),
        to_code=str(raw.get("toStnCode") or "").strip().upper(),
        to_name=_blank_to_none(raw.get("toStnName")),
        departure=str(raw.get("departureTime") or "").strip(),
        arrival=str(raw.get("arrivalTime") or "").strip(),
        departure_date=_blank_to_none(raw.get("departureDate")) or default_date,
        duration_min=duration or 0,
        distance=parse_int(raw.get("distance")),
        train_type=_blank_to_none(raw.get("trainType")),
        has_pantry=bool(raw.get("hasPantry", False)),
        rating=_to_float(raw.get("trainRating")),
        running_days_mask=_blank_to_none(raw.get("runningDays")),
        availability=_availability_list(raw),
    )


def map_search_result(
    payload: dict, *, src: str, dst: str, date: str
) -> TrainSearchResult:
    data = (payload or {}).get("data") or {}
    train_list = data.get("trainList") or []
    nearby = data.get("nearbyTrains") or []
    return TrainSearchResult(
        source_code=src.upper(),
        destination_code=dst.upper(),
        date=date,
        source_name=_blank_to_none(data.get("sourceStationName")),
        destination_name=_blank_to_none(data.get("destinationStationName")),
        quotas=[str(q) for q in (data.get("quotaList") or [])],
        trains=[map_train(t, default_date=date) for t in train_list],
        nearby_trains=[map_train(t, default_date=date) for t in nearby],
        from_cache=bool(data.get("fromCache", False)),
    )


# --------------------------------------------------------------------------- #
# Route / schedule
# --------------------------------------------------------------------------- #
def map_stop(raw: StopRaw, *, is_major: bool) -> Stop:
    return Stop(
        code=str(raw.get("StationCode") or "").strip().upper(),
        name=str(raw.get("StationName") or "").strip(),
        arrival=_blank_to_none(raw.get("ArrivalTime")),
        departure=_blank_to_none(raw.get("DepartureTime")),
        day=parse_int(raw.get("Day")) or 1,
        halt_min=parse_int(raw.get("HaltMinutes")),
        distance_from_origin=_to_float(raw.get("Distance")),
        platform=_blank_to_none(raw.get("ExpectedPlatformNo")),
        is_major=is_major,
    )


def map_route(payload: ScheduleRaw) -> TrainRoute:
    schedule = payload.get("Schedule") or []
    stops: list[Stop] = []
    last_code: str | None = None

    for major in schedule:
        stop = map_stop(major, is_major=True)
        if stop.code and stop.code != last_code:
            stops.append(stop)
            last_code = stop.code
        for inter in major.get("intermediateStations") or []:
            minor = map_stop(inter, is_major=False)
            if minor.code and minor.code != last_code:
                stops.append(minor)
                last_code = minor.code

    return TrainRoute(
        number=str(payload.get("TrainNo") or payload.get("TrainNumberString") or "").strip(),
        name=str(payload.get("TrainName") or "").strip(),
        from_code=str(payload.get("SourceCode") or "").strip().upper(),
        to_code=str(payload.get("DestinationCode") or "").strip().upper(),
        train_type=_blank_to_none(payload.get("TrainType")),
        total_duration_min=parse_duration_to_minutes(payload.get("TotalDuration")),
        has_pantry=bool(payload.get("HasPantry", False)),
        stops=stops,
    )


# --------------------------------------------------------------------------- #
# Bus / flight (reverse-engineered; tolerant to several key spellings)
# --------------------------------------------------------------------------- #
def _first(raw: dict, *keys: str) -> object:
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


def map_bus(raw: dict) -> "BusTrip":
    from ...models.domain import BusTrip

    return BusTrip(
        operator=str(
            _first(raw, "operator", "travels", "operatorName", "busOperator") or "Unknown"
        ).strip(),
        bus_type=_blank_to_none(_first(raw, "busType", "type", "coachType")),
        departure=_blank_to_none(_first(raw, "departureTime", "departure", "boardingTime")),
        arrival=_blank_to_none(_first(raw, "arrivalTime", "arrival", "droppingTime")),
        duration_min=parse_duration_to_minutes(_first(raw, "duration", "durationMinutes")),
        fare=parse_int(_first(raw, "fare", "price", "minFare", "startingFare")),
        seats_available=parse_int(_first(raw, "seatsAvailable", "availableSeats", "seats")),
    )


def map_flight(raw: dict) -> "Flight":
    from ...models.domain import Flight

    return Flight(
        airline=str(_first(raw, "airline", "airlineName", "carrier") or "Unknown").strip(),
        flight_number=_blank_to_none(_first(raw, "flightNumber", "flightNo", "number")),
        departure=_blank_to_none(_first(raw, "departureTime", "departure", "depTime")),
        arrival=_blank_to_none(_first(raw, "arrivalTime", "arrival", "arrTime")),
        duration_min=parse_duration_to_minutes(_first(raw, "duration", "durationMinutes")),
        stops=parse_int(_first(raw, "stops", "stopCount")) or 0,
        fare=parse_int(_first(raw, "fare", "price", "minFare")),
    )
