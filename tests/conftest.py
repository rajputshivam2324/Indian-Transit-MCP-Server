"""Shared test fixtures: real captured ConfirmTkt payloads + in-memory fakes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _isolate_from_host_environment(monkeypatch):
    """Settings read TRANSIT_*, and the platform variables PORT / RENDER steer the server's
    host and port. None of a developer's (or a CI host's) values may leak into a test."""
    for name in [n for n in os.environ if n.startswith("TRANSIT_") or n in ("PORT", "RENDER")]:
        monkeypatch.delenv(name)


@pytest.fixture
def search_payload() -> dict:
    return load_fixture("search_ndls_mmct.json")


@pytest.fixture
def autosuggest_payload() -> dict:
    return load_fixture("autosuggest_ndls.json")


@pytest.fixture
def schedule_payload() -> dict:
    return load_fixture("schedule_12951.json")
