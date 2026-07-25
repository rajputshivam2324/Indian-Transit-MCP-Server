"""Pure helper functions: date normalization, time math, numeric parsing.

Kept dependency-free and side-effect-free so they are trivial to unit test.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .infra.errors import InvalidDateError

_API_DATE = "%d-%m-%Y"
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})")


def normalize_date(value: str, *, today: date | None = None) -> str:
    """Normalize a user date into ConfirmTkt's ``DD-MM-YYYY`` format.

    Accepts ``DD-MM-YYYY``, ``YYYY-MM-DD``, ``DD/MM/YYYY``, and the keywords
    ``today`` / ``tomorrow``. Raises :class:`InvalidDateError` otherwise.
    """
    if not value or not value.strip():
        raise InvalidDateError("Date is required")
    raw = value.strip().lower()
    base = today or date.today()

    if raw in ("today", "now"):
        return base.strftime(_API_DATE)
    if raw == "tomorrow":
        return (base + timedelta(days=1)).strftime(_API_DATE)

    for fmt in (_API_DATE, "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime(_API_DATE)
        except ValueError:
            continue
    raise InvalidDateError(f"Unrecognized date format: {value!r}")


def add_days(api_date: str, days: int) -> str:
    """Return ``api_date`` (DD-MM-YYYY) shifted by ``days``."""
    d = datetime.strptime(api_date, _API_DATE).date()
    return (d + timedelta(days=days)).strftime(_API_DATE)


def parse_time_to_minutes(value: str | None) -> int | None:
    """Parse ``HH:MM`` into minutes since midnight, or ``None`` if unparseable."""
    if not value:
        return None
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm


def absolute_minutes(day: int, hhmm: str | None) -> int | None:
    """Convert a (day, HH:MM) pair into absolute minutes from journey start.

    ``day`` is 1-based (day 1 = departure day), matching the schedule payload.
    """
    tod = parse_time_to_minutes(hhmm)
    if tod is None:
        return None
    return (max(day, 1) - 1) * 24 * 60 + tod


def parse_duration_to_minutes(value: object) -> int | None:
    """Parse a duration into minutes.

    Handles ``"15:32"`` (H:MM), ``"15h 32m"`` / ``"15h32m"``, and bare integers
    (already minutes).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    s = str(value).strip().lower()
    if not s:
        return None
    if ":" in s:
        hh, _, mm = s.partition(":")
        h, m = parse_int(hh), parse_int(mm)
        if h is not None and m is not None:
            return h * 60 + m
    hm = re.match(r"(\d+)\s*h\s*(\d+)?\s*m?", s)
    if hm:
        h = int(hm.group(1))
        m = int(hm.group(2)) if hm.group(2) else 0
        return h * 60 + m
    return parse_int(s)


def parse_int(value: object) -> int | None:
    """Best-effort parse of an int from int/float/str (e.g. fare ``"2340"``)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().replace(",", "")
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else None


def extract_percentage(value: object) -> int | None:
    """Extract a 0-100 integer from values like ``56``, ``"56"``, ``"56% Chance"``."""
    n = parse_int(value)
    if n is None:
        return None
    return max(0, min(100, n))
