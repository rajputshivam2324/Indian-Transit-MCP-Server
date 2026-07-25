"""In-memory TTL cache with single-flight semantics.

Used for station lookups and routes (long TTL) and searches (short TTL). Concurrent
callers requesting the same missing key share one in-flight computation so we never
issue duplicate upstream requests during fan-out (``asyncio.gather``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

_Clock = Callable[[], float]


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    """A minimal async-aware TTL cache keyed by string."""

    def __init__(self, ttl: float, *, clock: _Clock = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._data: dict[str, _Entry[T]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> T | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._data.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: T, ttl: float | None = None) -> None:
        self._data[key] = _Entry(value, self._clock() + (ttl if ttl is not None else self._ttl))

    async def get_or_set(
        self, key: str, factory: Callable[[], Awaitable[T]], ttl: float | None = None
    ) -> T:
        """Return a cached value or compute it once, sharing in-flight work."""
        cached = self.get(key)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Re-check: another coroutine may have populated it while we waited.
            cached = self.get(key)
            if cached is not None:
                return cached
            value = await factory()
            self.set(key, value, ttl)
            return value
        # (lock intentionally left in the map; key space is small and bounded)

    def clear(self) -> None:
        self._data.clear()
        self._locks.clear()
