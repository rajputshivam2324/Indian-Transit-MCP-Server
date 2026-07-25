"""Domain exceptions carrying stable :class:`ErrorCode` values.

Services and providers raise these; the tool layer catches :class:`TransitError`
and turns it into an ``ErrorResult`` envelope so tool calls never crash.
"""

from __future__ import annotations

from ..models.envelopes import ErrorCode


class TransitError(Exception):
    """Base class for all expected, recoverable errors."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.provider = provider


class InvalidInputError(TransitError):
    code = ErrorCode.INVALID_INPUT


class InvalidDateError(TransitError):
    code = ErrorCode.INVALID_DATE


class StationNotFoundError(TransitError):
    code = ErrorCode.STATION_NOT_FOUND


class NoResultsError(TransitError):
    code = ErrorCode.NO_RESULTS


class FeatureDisabledError(TransitError):
    code = ErrorCode.FEATURE_DISABLED


class UpstreamUnavailableError(TransitError):
    """The upstream provider timed out, refused, or returned 5xx after retries."""

    code = ErrorCode.UPSTREAM_UNAVAILABLE


class UpstreamError(TransitError):
    """The upstream provider responded but with an error payload/unexpected shape."""

    code = ErrorCode.UPSTREAM_ERROR
