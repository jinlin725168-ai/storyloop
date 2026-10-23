"""Declarative, versioned policy for the change natures supported by this engine."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from .errors import StoryLoopError


@dataclass(frozen=True)
class ChangeType:
    criteria_floor: tuple[str, ...]
    writable_scope: tuple[str, ...]
    required_stages: tuple[str, ...]
    minimum_tier: str
    human_review: str
    id_prefix: str


CHANGE_TYPES: Mapping[str, ChangeType] = MappingProxyType({
    "feature": ChangeType(
        ("acceptance_red", "existing_file_characterization"),
        ("mutable", "test_roots"), ("by_tier",), "light", "by_tier", "FEAT"),
    "fix": ChangeType(
        ("defect_reproduction_red", "existing_file_characterization"),
        ("mutable", "test_roots"), ("by_tier",), "light", "by_tier", "FIX"),
    "refactor": ChangeType(
        ("structural_red", "behavior_green"),
        ("mutable", "test_roots"), ("by_tier", "characterization"),
        "standard", "by_tier", "REF"),
})


def type_of(product: dict) -> str:
    """Legacy contracts with no nature are feature changes."""
    return product.get("nature", "feature")


def definition_for(type_name: str) -> ChangeType:
    """Return the policy for a known nature, rejecting unsupported ones safely."""
    if type_name not in CHANGE_TYPES:
        raise StoryLoopError("Invalid change nature")
    return CHANGE_TYPES[type_name]
