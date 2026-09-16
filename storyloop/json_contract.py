"""Bounded, interoperable JSON encoding for external process contracts."""

from __future__ import annotations

import json

from .models import StoryLoopError, require

DEFAULT_MAX_BYTES = 1024 * 1024


def dumps_strict_json(value: object, label: str,
                      max_bytes: int = DEFAULT_MAX_BYTES,
                      sort_keys: bool = False) -> str:
    """Serialize finite standard JSON with an explicit UTF-8 byte bound."""
    try:
        serialized = json.dumps(value, sort_keys=sort_keys, ensure_ascii=False,
                                allow_nan=False, separators=(",", ":"))
        size = len(serialized.encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise StoryLoopError(f"{label} must be bounded JSON") from None
    require(size <= max_bytes, f"{label} must be bounded JSON")
    return serialized


def loads_strict_json(serialized: str, label: str,
                      max_bytes: int = DEFAULT_MAX_BYTES) -> object:
    """Parse one standard JSON value while rejecting duplicate object fields."""
    try:
        require(len(serialized.encode("utf-8")) <= max_bytes,
                f"{label} exceeded the output limit")
        return json.loads(serialized, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except StoryLoopError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError, UnicodeError):
        raise StoryLoopError(f"{label} must be exactly one valid JSON value") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        require(key not in value, "JSON contains duplicate fields")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise StoryLoopError("JSON must not contain non-finite numbers")