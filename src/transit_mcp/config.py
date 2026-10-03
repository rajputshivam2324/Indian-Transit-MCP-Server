"""Environment-driven configuration.

All settings are overridable via ``TRANSIT_*`` environment variables (or a local
``.env`` file). Sensible defaults mean the server runs with zero configuration.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> list[str]:
    """``"a, b,,c"`` -> ``["a", "b", "c"]``: env vars carry lists as plain comma text."""
    return [part.strip() for part in value.split(",") if part.strip()]


def _default_server_host() -> str:
    """All interfaces on a hosting platform, loopback everywhere else.

    Render, Heroku, Railway, Cloud Run and similar put a proxy in front of the app and hand it
    a ``PORT`` to listen on. That proxy cannot reach a loopback-only listener (Render reports
    "no open ports detected on 0.0.0.0"), so the server has to bind 0.0.0.0 there. Render also
    sets ``RENDER=true``. ``TRANSIT_SERVER_HOST`` overrides this either way.
    """
    hosted = os.environ.get("PORT") or os.environ.get("RENDER")
    return "0.0.0.0" if hosted else "127.0.0.1"


class Settings(BaseSettings):
    """Immutable application settings."""

    model_config = SettingsConfigDict(
        env_prefix="TRANSIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # server_port also answers to the platform-standard PORT (an alias), so constructing
        # Settings(server_port=...) by field name has to keep working.
        populate_by_name=True,
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

    # --- MCP server (how clients connect to this process) --------------------
    # "streamable-http" serves MCP at http://<server_host>:<server_port><server_path>;
    # "stdio" is for clients that launch the process themselves. The server has no
    # authentication of its own, so it listens on loopback unless it is told otherwise - or
    # it is running on a hosting platform (PORT / RENDER set), which needs 0.0.0.0.
    transport: Literal["streamable-http", "stdio"] = "streamable-http"
    server_host: str = Field(default_factory=_default_server_host)
    # TRANSIT_SERVER_PORT wins; the platform-standard PORT is the fallback.
    server_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("TRANSIT_SERVER_PORT", "PORT"),
    )
    server_path: str = "/mcp"
    # Extra Host / Origin header values to accept (comma-separated), on top of the loopback
    # ones. Needed behind a reverse proxy that forwards the public hostname, and the only way
    # to turn DNS-rebinding protection on when server_host is not loopback.
    allowed_hosts: str = ""
    allowed_origins: str = ""

    # --- Logging -------------------------------------------------------------
    log_level: str = "INFO"

    @field_validator("server_path")
    @classmethod
    def _normalize_server_path(cls, value: str) -> str:
        """Always a leading slash and no trailing one (``mcp`` -> ``/mcp``, ``""`` -> ``/``)."""
        return "/" + value.strip().strip("/")

    @property
    def allowed_host_list(self) -> list[str]:
        return _split_csv(self.allowed_hosts)

    @property
    def allowed_origin_list(self) -> list[str]:
        return _split_csv(self.allowed_origins)

    def resolved_device_id(self) -> str:
        """Return the configured device id, generating a stable random one if unset."""
        return self.device_id or str(uuid.uuid4())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
