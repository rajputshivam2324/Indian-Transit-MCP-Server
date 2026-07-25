"""Reusable, framework-agnostic business logic.

Import these directly (with an injected provider) to use the core without MCP::

    from transit_mcp.services import TrainSearchService
"""

from __future__ import annotations

from .planning import MultiModalService, SplitJourneyService
from .ranking import (
    TrainFilters,
    apply_preferred,
    best_confirm_chance,
    class_is_available,
    filter_trains,
    min_fare,
    sort_trains,
)
from .routes import (
    NearbyStation,
    NearbyStationService,
    NearbyStationsResult,
    RouteService,
)
from .stations import StationService, score_station
from .trains import (
    AvailabilityService,
    ConfirmationResult,
    ConfirmationService,
    SearchResponse,
    TrainSearchService,
)

__all__ = [
    "StationService",
    "score_station",
    "TrainSearchService",
    "SearchResponse",
    "AvailabilityService",
    "ConfirmationService",
    "ConfirmationResult",
    "RouteService",
    "NearbyStationService",
    "NearbyStation",
    "NearbyStationsResult",
    "SplitJourneyService",
    "MultiModalService",
    "TrainFilters",
    "filter_trains",
    "sort_trains",
    "apply_preferred",
    "min_fare",
    "best_confirm_chance",
    "class_is_available",
]
