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
    requires_new_criteria: bool


CHANGE_TYPES: Mapping[str, ChangeType] = MappingProxyType({
    "feature": ChangeType(
        ("acceptance_red", "existing_file_characterization"),
        ("mutable", "test_roots"), ("by_tier",), "light", "by_tier", "FEAT", True),
    "fix": ChangeType(
        ("defect_reproduction_red", "existing_file_characterization"),
        ("mutable", "test_roots"), ("by_tier",), "light", "by_tier", "FIX", False),
    "refactor": ChangeType(
        ("structural_red", "behavior_green"),
        ("mutable", "test_roots"), ("by_tier", "characterization"),
        "standard", "by_tier", "REF", False),
})


def type_of(product: dict) -> str:
    """Legacy contracts with no nature are feature changes."""
    return product.get("nature", "feature")


def definition_for(type_name: str) -> ChangeType:
    """Return the policy for a known nature, rejecting unsupported ones safely."""
    if not isinstance(type_name, str) or type_name not in CHANGE_TYPES:
        raise StoryLoopError("Invalid change nature")
    return CHANGE_TYPES[type_name]


def type_for_change_id(change_id: str) -> str:
    """Identify a declared ID prefix; unknown legacy IDs retain feature policy."""
    for type_name, definition in CHANGE_TYPES.items():
        if change_id.startswith(definition.id_prefix + "-"):
            return type_name
    return "feature"
