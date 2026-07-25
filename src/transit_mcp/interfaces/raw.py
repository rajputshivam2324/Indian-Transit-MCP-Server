"""Raw upstream payload aliases.

Providers fetch these loosely-typed JSON objects and hand them to mappers, which
translate them into the strongly-typed domain models. Keeping the raw shape as a
plain mapping keeps providers resilient to additive/renamed upstream fields.
"""

from __future__ import annotations

from typing import Any

# Each alias is a single JSON object from the corresponding upstream response.
StationRaw = dict[str, Any]  # one element of data.stationList
TrainRaw = dict[str, Any]  # one element of data.trainList
AvailabilityRaw = dict[str, Any]  # one value of trainList[].availabilityCache
ScheduleRaw = dict[str, Any]  # the full /trains/schedule response
StopRaw = dict[str, Any]  # one element of Schedule[] (or intermediateStations[])
BusRaw = dict[str, Any]
FlightRaw = dict[str, Any]
