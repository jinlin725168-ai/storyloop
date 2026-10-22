"""Deterministic lifecycle checks; G1–G3 move here in Tasks 4 and 5."""
from .models import require


def guard_updates(value: dict) -> None:
    """A caller or delivery effect cannot smuggle a second status assignment."""
    require(isinstance(value, dict) and "status" not in value,
            "Transition updates must be a dictionary without status")
