"""Composition root.

``build_container`` is the single place where concrete providers are constructed
and injected into services. Both the MCP entrypoint (``main.py``) and external
agents can call it to obtain ready-to-use services without touching MCP.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings, get_settings
from ..infra.cache import TTLCache
from ..infra.http import HttpClient
from ..infra.logging import configure_logging
from ..providers.confirmtkt import (
    ConfirmTktBusProvider,
    ConfirmTktFlightProvider,
    ConfirmTktRouteProvider,
    ConfirmTktStationProvider,
    ConfirmTktTrainProvider,
)
from ..providers.estimators import PassthroughConfirmationEstimator
from ..services.planning import MultiModalService, SplitJourneyService
from ..services.routes import NearbyStationService, RouteService
from ..services.stations import StationService
from ..services.trains import (
    AvailabilityService,
    ConfirmationService,
    TrainSearchService,
)


@dataclass
class Container:
    """Holds wired services and owns the shared HTTP client lifecycle."""

    settings: Settings
    http: HttpClient
    stations: StationService
    train_search: TrainSearchService
    availability: AvailabilityService
    confirmation: ConfirmationService
    routes: RouteService
    nearby: NearbyStationService
    split: SplitJourneyService
    multimodal: MultiModalService

    async def aclose(self) -> None:
        await self.http.aclose()


def build_http_client(settings: Settings) -> HttpClient:
    return HttpClient(
        settings.confirmtkt_base_url,
        headers={
            "clientid": settings.client_id,
            "apikey": settings.api_key,
            "deviceid": settings.resolved_device_id(),
            "content-type": "application/json",
            "User-Agent": settings.user_agent,
        },
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        backoff=settings.http_backoff_seconds,
    )


def build_container(
    settings: Settings | None = None,
    *,
    http: HttpClient | None = None,
) -> Container:
    """Construct and wire all providers and services (dependency injection)."""
    s = settings or get_settings()
    configure_logging(s.log_level)

    http = http or build_http_client(s)

    station_provider = ConfirmTktStationProvider(http)
    train_provider = ConfirmTktTrainProvider(http)
    route_provider = ConfirmTktRouteProvider(http)
    bus_provider = ConfirmTktBusProvider(http) if s.enable_bus else None
    flight_provider = ConfirmTktFlightProvider(http) if s.enable_flight else None
    estimator = PassthroughConfirmationEstimator()

    stations = StationService(
        station_provider,
        cache=TTLCache(s.station_cache_ttl),
        cache_ttl=s.station_cache_ttl,
    )
    train_search = TrainSearchService(
        train_provider,
        stations,
        cache=TTLCache(s.search_cache_ttl),
        cache_ttl=s.search_cache_ttl,
        default_limit=s.default_search_limit,
    )
    availability = AvailabilityService(train_search)
    confirmation = ConfirmationService(availability, estimator)
    routes = RouteService(
        route_provider,
        cache=TTLCache(s.route_cache_ttl),
        cache_ttl=s.route_cache_ttl,
    )
    nearby = NearbyStationService(stations)
    split = SplitJourneyService(
        stations,
        train_search,
        routes,
        max_hubs=s.split_max_hubs,
        top_n=s.split_top_n,
        default_max_layover_hours=s.split_max_layover_hours,
        default_min_transfer_min=s.split_min_transfer_min,
    )
    multimodal = MultiModalService(
        train_search,
        bus_provider=bus_provider,
        flight_provider=flight_provider,
        enable_bus=s.enable_bus,
        enable_flight=s.enable_flight,
    )

    return Container(
        settings=s,
        http=http,
        stations=stations,
        train_search=train_search,
        availability=availability,
        confirmation=confirmation,
        routes=routes,
        nearby=nearby,
        split=split,
        multimodal=multimodal,
    )
