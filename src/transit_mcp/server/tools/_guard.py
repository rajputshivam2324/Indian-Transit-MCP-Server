"""Shared helpers for tool modules: error guarding and enum coercion."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.types import ToolAnnotations

from ...infra.errors import TransitError
from ...infra.logging import get_logger
from ...models.envelopes import ErrorCode, err

log = get_logger("tools")

# Every tool in this server is a side-effect-free lookup over external data.
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def tool_guard(fn: F) -> F:
    """Wrap an async tool so it always returns an envelope, never raises."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except TransitError as exc:
            return err(exc.code, exc.message, exc.provider)
        except Exception as exc:  # noqa: BLE001 - tools must never crash the client
            log.exception("tool %s failed", fn.__name__)
            return err(ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    return wrapper  # type: ignore[return-value]


def coerce_enum(enum_cls: type, value: str | None, default: Any) -> Any:
    """Best-effort parse of a string into an enum member (case-insensitive)."""
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        pass
    v = str(value).strip().lower()
    for member in enum_cls:
        if member.value.lower() == v or member.name.lower() == v:
            return member
    return default


def dump(model: Any) -> dict[str, Any]:
    """JSON-serialize a pydantic model, omitting null fields.

    Dropping ``None`` values is a lossless compaction (absent == not-applicable),
    which meaningfully shrinks payloads for small-context models without removing
    any decision-relevant data.
    """
    return model.model_dump(mode="json", exclude_none=True)


def dump_all(models: Any) -> list[dict[str, Any]]:
    return [dump(m) for m in models]
