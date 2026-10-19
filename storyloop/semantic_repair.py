"""Retry one answer that passed its role schema but failed engine semantics."""

from __future__ import annotations

from typing import Callable

from .models import StoryLoopError

REPAIR_KEYS = ("previous_response", "validation_errors")
REPAIRABLE_CODES = frozenset({"BLOCKED"})


def answer_with_repair(
    call: Callable[[dict], object],
    request: dict,
    validate: Callable[[object], None],
) -> object:
    """Return a valid answer, making at most one new call after a semantic failure."""
    answer = call(request)
    try:
        validate(answer)
    except StoryLoopError as exc:
        if exc.code not in REPAIRABLE_CODES:
            raise
        retry_request = {
            **request,
            "previous_response": answer,
            "validation_errors": [str(exc)],
        }
        answer = call(retry_request)
        validate(answer)
    return answer
