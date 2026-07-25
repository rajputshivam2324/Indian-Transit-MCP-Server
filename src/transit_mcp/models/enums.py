"""Enumerations shared across models, services, and tools."""

from __future__ import annotations

from enum import Enum


class TravelClass(str, Enum):
    """Indian Railways reserved travel classes (IRCTC codes)."""

    AC_FIRST = "1A"  # AC First Class
    AC_2_TIER = "2A"  # AC 2 Tier
    AC_3_TIER = "3A"  # AC 3 Tier
    AC_3_ECONOMY = "3E"  # AC 3 Economy
    CHAIR_CAR = "CC"  # AC Chair Car
    EXEC_CHAIR = "EC"  # Executive Chair Car / Anubhuti
    SLEEPER = "SL"  # Sleeper (non-AC)
    SECOND_SITTING = "2S"  # Second Sitting (reserved)
    FIRST_CLASS = "FC"  # First Class (non-AC)

    @property
    def label(self) -> str:
        return _CLASS_LABELS.get(self, self.value)


_CLASS_LABELS: dict[TravelClass, str] = {
    TravelClass.AC_FIRST: "AC First Class (1A)",
    TravelClass.AC_2_TIER: "AC 2 Tier (2A)",
    TravelClass.AC_3_TIER: "AC 3 Tier (3A)",
    TravelClass.AC_3_ECONOMY: "AC 3 Economy (3E)",
    TravelClass.CHAIR_CAR: "AC Chair Car (CC)",
    TravelClass.EXEC_CHAIR: "Executive Chair Car (EC)",
    TravelClass.SLEEPER: "Sleeper (SL)",
    TravelClass.SECOND_SITTING: "Second Sitting (2S)",
    TravelClass.FIRST_CLASS: "First Class (FC)",
}


class Quota(str, Enum):
    """Booking quotas recognised by IRCTC / ConfirmTkt."""

    GENERAL = "GN"
    TATKAL = "TQ"
    PREMIUM_TATKAL = "PT"
    LADIES = "LD"
    SENIOR_CITIZEN = "SS"
    LOWER_BERTH = "LB"
    DUTY_PASS = "DP"


class SortBy(str, Enum):
    """Sort orders supported by the train search service (applied client-side)."""

    DEFAULT = "default"
    DEPARTURE = "departure"
    ARRIVAL = "arrival"
    DURATION = "duration"
    PRICE = "price"
    CONFIRMATION = "confirmation"
    DISTANCE = "distance"


class TransportMode(str, Enum):
    """Supported transport modes for multi-modal planning."""

    TRAIN = "train"
    BUS = "bus"
    FLIGHT = "flight"
