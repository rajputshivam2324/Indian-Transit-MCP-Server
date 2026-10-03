"""Provider-agnostic domain models, enums, and result envelopes."""

from __future__ import annotations

from .domain import (
    BusTrip,
    ClassAvailability,
    Flight,
    JourneyLeg,
    SplitItinerary,
    Station,
    Stop,
    Train,
    TrainRoute,
    TrainSearchResult,
    TripOption,
)
from .enums import Quota, SortBy, TransportMode, TravelClass
from .envelopes import (
    ErrorCode,
    ErrorDetail,
    ErrorResult,
    Result,
    err,
    ok,
)
from .segments import (
    BestSegment,
    BookingSegmentReport,
    SegmentClassFare,
    SegmentStop,
    SegmentSuggestion,
    SegmentUserLeg,
    TrainSegmentResult,
)

__all__ = [
    # enums
    "TravelClass",
    "Quota",
    "SortBy",
    "TransportMode",
    # domain
    "Station",
    "ClassAvailability",
    "Train",
    "TrainSearchResult",
    "Stop",
    "TrainRoute",
    "JourneyLeg",
    "SplitItinerary",
    "BusTrip",
    "Flight",
    "TripOption",
    # booking segments
    "SegmentClassFare",
    "SegmentUserLeg",
    "SegmentSuggestion",
    "SegmentStop",
    "TrainSegmentResult",
    "BestSegment",
    "BookingSegmentReport",
    # envelopes
    "Result",
    "ErrorResult",
    "ErrorDetail",
    "ErrorCode",
    "ok",
    "err",
]
