"""Station resolution service: free text -> ranked station candidates."""

from __future__ import annotations

import re

from ..infra.cache import TTLCache
from ..infra.errors import StationNotFoundError
from ..interfaces.providers import StationProvider
from ..models.domain import Station

_CODE_RE = re.compile(r"^[A-Z]{2,5}$")


def score_station(station: Station, query: str) -> int:
    """Higher is a better match for ``query`` (used to rank autosuggest results)."""
    q = query.strip().lower()
    code = station.code.lower()
    name = station.name.lower()
    city = (station.city or "").lower()

    score = 0
    if code == q:
        score += 100
    elif code.startswith(q):
        score += 60
    if name == q or city == q:
        score += 50
    elif name.startswith(q) or city.startswith(q):
        score += 30
    elif q in name or q in city:
        score += 15
    if station.is_major:
        score += 8
    return score


class StationService:
    """Resolve user queries to stations via a :class:`StationProvider`."""

    def __init__(
        self,
        provider: StationProvider,
        *,
        cache: TTLCache[list[Station]] | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._cache_ttl = cache_ttl

    async def autosuggest(self, query: str) -> list[Station]:
        key = f"station:{query.strip().lower()}"
        if self._cache is not None:
            return await self._cache.get_or_set(
                key, lambda: self._provider.autosuggest(query), self._cache_ttl
            )
        return await self._provider.autosuggest(query)

    async def resolve(self, query: str) -> tuple[Station, list[Station]]:
        """Return the best-matching station and the full ranked candidate list."""
        if not query or not query.strip():
            raise StationNotFoundError("Empty station query")

        candidates = await self.autosuggest(query)
        if not candidates:
            raise StationNotFoundError(f"No station found for {query!r}")

        ranked = sorted(candidates, key=lambda s: score_station(s, query), reverse=True)

        # If the query is itself a station code that appears in the results, trust it.
        q_upper = query.strip().upper()
        if _CODE_RE.match(q_upper):
            exact = next((s for s in ranked if s.code == q_upper), None)
            if exact is not None:
                ranked = [exact] + [s for s in ranked if s.code != q_upper]

        return ranked[0], ranked

    async def resolve_code(self, query: str) -> str:
        best, _ = await self.resolve(query)
        return best.code
