"""Result envelopes returned by tools.

Every tool returns either a :class:`Result` (``ok=True``) or an :class:`ErrorResult`
(``ok=False``). Tools never raise across the MCP boundary; failures are normalized
into ``ErrorResult`` so a single failing provider can't crash the client.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorCode(str, Enum):
    """Stable, machine-readable error codes."""

    STATION_NOT_FOUND = "STATION_NOT_FOUND"
    INVALID_DATE = "INVALID_DATE"
    INVALID_INPUT = "INVALID_INPUT"
    NO_RESULTS = "NO_RESULTS"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    provider: str | None = None


class Result(BaseModel, Generic[T]):
    """Successful result carrying typed ``data`` and free-form ``meta``."""

    ok: Literal[True] = True
    data: T
    meta: dict[str, Any] = Field(default_factory=dict)


class ErrorResult(BaseModel):
    """Failed result carrying a structured error."""

    ok: Literal[False] = False
    error: ErrorDetail


def ok(data: Any, **meta: Any) -> dict[str, Any]:
    """Build a success envelope as a JSON-ready dict."""
    return Result(data=data, meta=meta).model_dump(mode="json")


def err(
    code: ErrorCode | str,
    message: str,
    provider: str | None = None,
) -> dict[str, Any]:
    """Build an error envelope as a JSON-ready dict."""
    if isinstance(code, str) and not isinstance(code, ErrorCode):
        try:
            code = ErrorCode(code)
        except ValueError:
            code = ErrorCode.INTERNAL_ERROR
    return ErrorResult(
        error=ErrorDetail(code=code, message=message, provider=provider)
    ).model_dump(mode="json")
