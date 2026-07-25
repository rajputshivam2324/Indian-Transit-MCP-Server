"""Environment-driven configuration.

All settings are overridable via ``TRANSIT_*`` environment variables (or a local
``.env`` file). Sensible defaults mean the server runs with zero configuration.
"""

from __future__ import annotations

import uuid
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Immutable application settings."""

    model_config = SettingsConfigDict(
        env_prefix="TRANSIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- ConfirmTkt upstream -------------------------------------------------
    confirmtkt_base_url: str = "https://cttrainsapi.confirmtkt.com"
    client_id: str = "ct-web"
    api_key: str = "ct-web!2$"
    device_id: str = ""
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # --- HTTP behaviour ------------------------------------------------------
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 2
    http_backoff_seconds: float = 0.5

    # --- Cache TTLs (seconds) ------------------------------------------------
    station_cache_ttl: int = 86_400
    route_cache_ttl: int = 86_400
    search_cache_ttl: int = 120

    # --- Search / planning defaults -----------------------------------------
    default_search_limit: int = 20
    split_max_hubs: int = Field(default=6, ge=1)
    split_top_n: int = Field(default=5, ge=1)
    split_max_layover_hours: float = 6.0
    split_min_transfer_min: int = 30

    # --- Feature flags -------------------------------------------------------
    enable_bus: bool = False
    enable_flight: bool = False

    # --- Logging -------------------------------------------------------------
    log_level: str = "INFO"

    def resolved_device_id(self) -> str:
        """Return the configured device id, generating a stable random one if unset."""
        return self.device_id or str(uuid.uuid4())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
