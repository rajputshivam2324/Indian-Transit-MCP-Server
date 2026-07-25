"""Shared async HTTP client with bounded retry/backoff.

Wraps a single :class:`httpx.AsyncClient`. Retries idempotent GETs on transient
failures (timeouts, connection errors, HTTP 5xx) with exponential backoff, and
normalizes terminal failures into :class:`TransitError` subclasses.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .errors import UpstreamError, UpstreamUnavailableError
from .logging import get_logger

log = get_logger("http")

_RETRYABLE_STATUS = {500, 502, 503, 504, 429}


class HttpClient:
    """Thin async wrapper around ``httpx.AsyncClient`` with retries."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
        max_retries: int = 2,
        backoff: float = 0.5,
        provider: str = "confirmtkt",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider = provider
        self._max_retries = max(0, max_retries)
        self._backoff = max(0.0, backoff)
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers or {},
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )

    async def get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET ``path`` and return parsed JSON, retrying transient failures."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.get(path, params=clean)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                log.warning(
                    "GET %s failed (attempt %d/%d): %s",
                    path,
                    attempt + 1,
                    self._max_retries + 1,
                    exc,
                )
                await self._sleep(attempt)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                log.warning(
                    "GET %s -> HTTP %d (attempt %d/%d)",
                    path,
                    resp.status_code,
                    attempt + 1,
                    self._max_retries + 1,
                )
                last_exc = UpstreamUnavailableError(
                    f"Upstream returned HTTP {resp.status_code}",
                    provider=self._provider,
                )
                await self._sleep(attempt)
                continue

            if resp.status_code >= 400:
                raise UpstreamError(
                    f"Upstream returned HTTP {resp.status_code} for {path}",
                    provider=self._provider,
                )

            try:
                return resp.json()
            except ValueError as exc:
                raise UpstreamError(
                    f"Upstream returned non-JSON response for {path}",
                    provider=self._provider,
                ) from exc

        raise UpstreamUnavailableError(
            f"Upstream unavailable for {path} after {self._max_retries + 1} attempts",
            provider=self._provider,
        ) from last_exc

    async def _sleep(self, attempt: int) -> None:
        if self._backoff:
            await asyncio.sleep(self._backoff * (2**attempt))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
