"""Shared test fixtures: real captured ConfirmTkt payloads + in-memory fakes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def search_payload() -> dict:
    return load_fixture("search_ndls_mmct.json")


@pytest.fixture
def autosuggest_payload() -> dict:
    return load_fixture("autosuggest_ndls.json")


@pytest.fixture
def schedule_payload() -> dict:
    return load_fixture("schedule_12951.json")
