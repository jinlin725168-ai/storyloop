"""Shared monotonic deadline helpers for one Stage attempt."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from .models import StoryLoopError, require


Clock = Callable[[], int]


def monotonic_ms() -> int:
    """Return the process monotonic clock in integer milliseconds."""
    return time.monotonic_ns() // 1_000_000


def ensure_time_remaining(deadline_ms: int | None,
                          clock_ms: Clock | None = None) -> None:
    """Fail before another operation starts when the shared deadline expired."""
    if deadline_ms is None:
        return
    remaining_seconds(deadline_ms, clock_ms)


def process_deadline(configured_seconds: int | float,
                     deadline_ms: int | None,
                     clock_ms: Clock | None = None) -> tuple[float, float | None]:
    """Return a bounded relative timeout and matching absolute deadline."""
    require(isinstance(configured_seconds, (int, float))
            and not isinstance(configured_seconds, bool)
            and math.isfinite(configured_seconds) and configured_seconds > 0,
            "Invalid process timeout")
    if deadline_ms is None:
        return float(configured_seconds), None
    remaining = remaining_seconds(deadline_ms, clock_ms)
    return min(float(configured_seconds), remaining), deadline_ms / 1000


def remaining_seconds(deadline_ms: int, clock_ms: Clock | None = None) -> float:
    """Validate and calculate positive time remaining on a monotonic deadline."""
    require(type(deadline_ms) is int and deadline_ms >= 0,
            "Invalid attempt deadline")
    clock = clock_ms or monotonic_ms
    require(callable(clock), "Invalid attempt budget clock")
    now_ms = clock()
    require(type(now_ms) is int and now_ms >= 0,
            "Invalid attempt budget clock value")
    remaining = (deadline_ms - now_ms) / 1000
    if remaining <= 0:
        raise StoryLoopError("Stage exceeded its time budget", code="TIME_BUDGET")
    return remaining
