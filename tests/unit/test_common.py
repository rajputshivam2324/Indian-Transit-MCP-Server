"""Unit tests for pure date/time/number helpers."""

from __future__ import annotations

from datetime import date

import pytest

from transit_mcp.common import (
    absolute_minutes,
    add_days,
    extract_percentage,
    normalize_date,
    parse_duration_to_minutes,
    parse_int,
    parse_time_to_minutes,
)
from transit_mcp.infra.errors import InvalidDateError


def test_normalize_date_formats():
    assert normalize_date("24-07-2026") == "24-07-2026"
    assert normalize_date("2026-07-24") == "24-07-2026"
    assert normalize_date("24/07/2026") == "24-07-2026"


def test_normalize_date_keywords():
    base = date(2026, 7, 24)
    assert normalize_date("today", today=base) == "24-07-2026"
    assert normalize_date("tomorrow", today=base) == "25-07-2026"


def test_normalize_date_invalid():
    with pytest.raises(InvalidDateError):
        normalize_date("not-a-date")
    with pytest.raises(InvalidDateError):
        normalize_date("")


def test_add_days():
    assert add_days("31-12-2026", 1) == "01-01-2027"


def test_parse_time_and_absolute_minutes():
    assert parse_time_to_minutes("22:40") == 1360
    assert parse_time_to_minutes("bad") is None
    assert parse_time_to_minutes("25:00") is None
    assert absolute_minutes(1, "10:00") == 600
    assert absolute_minutes(2, "10:00") == 600 + 1440


def test_parse_duration():
    assert parse_duration_to_minutes("15:32") == 15 * 60 + 32
    assert parse_duration_to_minutes("15h 32m") == 15 * 60 + 32
    assert parse_duration_to_minutes(1060) == 1060


def test_parse_int_and_percentage():
    assert parse_int("2,340") == 2340
    assert parse_int(None) is None
    assert parse_int(True) is None
    assert extract_percentage("56% Chance") == 56
    assert extract_percentage(150) == 100
