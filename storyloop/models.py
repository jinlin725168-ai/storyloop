"""Pure JSON contract primitives shared by the application."""
from __future__ import annotations

import re

from .errors import StoryLoopError, require


def text(value: object, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{label} must be nonempty text")
    require(len(value) <= 20000, f"{label} is too long")
    return value


def identifier(value: object, label: str) -> str:
    text(value, label)
    require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value)), f"Invalid {label}")
    return value


def strings(value: object, label: str) -> list:
    require(isinstance(value, list), f"{label} must be a list")
    for item in value:
        text(item, label)
    return value


def fields(value: object, required: set, optional: set | None = None) -> None:
    require(isinstance(value, dict), "Expected a JSON object")
    require(required <= value.keys(), f"Missing fields: {', '.join(sorted(required - value.keys()))}")
    require(value.keys() <= required | (optional or set()), "Unknown contract fields")
