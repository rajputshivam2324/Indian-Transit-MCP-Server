"""Confirmation estimators.

The default estimator is a passthrough over ConfirmTkt's own prediction. A future
heuristic/ML estimator can implement the same :class:`ConfirmationEstimator` port
and be injected at the composition root without touching any caller (OCP).
"""

from __future__ import annotations

from ..models.domain import ClassAvailability


class PassthroughConfirmationEstimator:
    """Return the availability unchanged (ConfirmTkt already predicts chance)."""

    def estimate(self, availability: ClassAvailability) -> ClassAvailability:
        return availability
