"""Concrete provider adapters (implement the ports in ``interfaces``)."""

from __future__ import annotations

from .estimators import PassthroughConfirmationEstimator

__all__ = ["PassthroughConfirmationEstimator"]
