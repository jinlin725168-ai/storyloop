"""Deterministic, side-effect-free routing policy evaluation."""
from __future__ import annotations

import math
from copy import deepcopy
from pathlib import PurePosixPath

from .change_types import definition_for
from .models import StoryLoopError


TIERS = ("light", "standard", "heavy")
DEFAULT_BUDGETS = {"light": 2, "standard": 3, "heavy": 4}
DEFAULT_POLICY = {
    "policy_version": 1,
    "split_stories": 6,
    "split_files": 15,
    "split_interfaces": 8,
    "heavy_stories": 4,
    "heavy_existing": 8,
    "heavy_references": 30,
    "manual_ratio": 0.25,
    "sensitive_paths": [],
    "guard_paths": [],
}
COUNT_FIELDS = ("stories", "existing_files", "created_files", "interfaces", "max_references")
RISK_FIELDS = (
    "sensitive", "migration", "external_contract", "new_interface", "crosscut",
    "principle_deviation", "hard_verify_high_cost", "dependency_swap", "persistent_state",
    "human_choice", "guard",
)
HEAVY_RISKS = RISK_FIELDS[:7]
STANDARD_RISKS = RISK_FIELDS[7:10]
SIGNAL_FIELDS = {"nature", "stories", "criteria", "manual", "existing_files",
                 "created_files", "interfaces", "max_references", "complete", "risks"}


def max_tier(*tiers: str) -> str:
    """Return the strongest known tier."""
    if not tiers or any(tier not in TIERS for tier in tiers):
        raise StoryLoopError("Invalid routing tier")
    return max(tiers, key=TIERS.index)


def validate_policy(value: dict | None) -> dict:
    """Fill routing defaults and reject unsafe or inconsistent policy values."""
    if value is not None and not isinstance(value, dict):
        raise StoryLoopError("Routing policy must be an object")
    supplied = {} if value is None else value
    if not set(supplied) <= set(DEFAULT_POLICY):
        raise StoryLoopError("Unknown routing policy fields")
    policy = deepcopy(DEFAULT_POLICY)
    policy.update(deepcopy(supplied))
    _validate_policy_scalars(policy)
    for field in ("sensitive_paths", "guard_paths"):
        _validate_paths(policy[field], field)
    return policy


def _validate_policy_scalars(policy: dict) -> None:
    integer_fields = ("policy_version", "split_stories", "split_files", "split_interfaces",
                      "heavy_stories", "heavy_existing", "heavy_references")
    for field in integer_fields:
        value = policy[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise StoryLoopError(f"Invalid routing policy field: {field}")
    if policy["policy_version"] != 1:
        raise StoryLoopError("Unsupported routing policy version")
    ratio = policy["manual_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise StoryLoopError("Invalid routing policy field: manual_ratio")
    if not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise StoryLoopError("Invalid routing policy field: manual_ratio")
    if policy["heavy_stories"] > policy["split_stories"]:
        raise StoryLoopError("heavy_stories exceeds split_stories")
    if policy["heavy_existing"] > policy["split_files"]:
        raise StoryLoopError("heavy_existing exceeds split_files")


def _validate_paths(value: object, field: str) -> None:
    if not isinstance(value, list):
        raise StoryLoopError(f"Invalid routing policy field: {field}")
    for item in value:
        if (not isinstance(item, str) or not item or "\\" in item or "\x00" in item
                or ":" in item):
            raise StoryLoopError(f"Unsafe routing path in {field}")
        path = PurePosixPath(item)
        if not path.parts or path.is_absolute() or "." in path.parts or ".." in path.parts:
            raise StoryLoopError(f"Unsafe routing path in {field}")


def evaluate(signals: dict, policy: dict) -> dict:
    """Evaluate validated Signals against a validated routing policy."""
    checked_policy = validate_policy(policy)
    _validate_signals(signals)
    unresolved = _unresolved(signals)
    tier = "light" if _can_be_light(signals, unresolved) else "standard"
    tier = max_tier(tier, definition_for(signals["nature"]).minimum_tier)
    reasons = [_base_reason(signals, tier, unresolved)]
    tier, split = _apply_size_rules(signals, checked_policy, tier, reasons)
    tier = _apply_risk_rules(signals, tier, reasons)
    if signals["manual"] / signals["criteria"] > checked_policy["manual_ratio"]:
        tier = max_tier(tier, "heavy")
        reasons.append(_reason("HEAVY-MANUAL", ["manual", "criteria"], signals))
    if signals["risks"]["guard"] == "yes":
        tier = TIERS[min(TIERS.index(tier) + 1, TIERS.index("heavy"))]
        reasons.append({"rule_id": "GUARD", "fields": ["risks.guard"], "values": ["yes"]})
    return {"tier": tier, "split": split, "reasons": reasons, "unresolved": unresolved}


def _validate_signals(value: object) -> None:
    if not isinstance(value, dict) or set(value) != SIGNAL_FIELDS:
        raise StoryLoopError("Invalid routing Signals fields")
    definition_for(value["nature"])
    for field in COUNT_FIELDS:
        item = value[field]
        if item is not None and (isinstance(item, bool) or not isinstance(item, int) or item < 0):
            raise StoryLoopError(f"Invalid Signals count: {field}")
    criteria, manual = value["criteria"], value["manual"]
    if isinstance(criteria, bool) or not isinstance(criteria, int) or criteria <= 0:
        raise StoryLoopError("Invalid Signals count: criteria")
    if isinstance(manual, bool) or not isinstance(manual, int) or not 0 <= manual <= criteria:
        raise StoryLoopError("Invalid Signals count: manual")
    if not isinstance(value["complete"], bool):
        raise StoryLoopError("Invalid Signals completeness")
    risks = value["risks"]
    if not isinstance(risks, dict) or set(risks) != set(RISK_FIELDS):
        raise StoryLoopError("Invalid routing risk fields")
    if any(item not in ("yes", "no", "unknown") for item in risks.values()):
        raise StoryLoopError("Invalid routing risk value")


def _unresolved(signals: dict) -> list[str]:
    result = [field for field in COUNT_FIELDS if signals[field] is None]
    result.extend(f"risks.{field}" for field in RISK_FIELDS
                  if signals["risks"][field] == "unknown")
    if not signals["complete"]:
        result.append("complete")
    return result


def _can_be_light(signals: dict, unresolved: list[str]) -> bool:
    return (not unresolved and signals["stories"] == 1
            and definition_for(signals["nature"]).minimum_tier == "light"
            and signals["existing_files"] <= 2
            and all(signals["risks"][field] == "no" for field in HEAVY_RISKS + STANDARD_RISKS))


def _base_reason(signals: dict, tier: str, unresolved: list[str]) -> dict:
    if tier == "light":
        fields = ["stories", "nature", "existing_files", "complete"]
        return _reason("LIGHT", fields, signals)
    fields = unresolved or ["stories", "nature", "existing_files"]
    values = [_field_value(signals, field) for field in fields]
    return {"rule_id": "DEFAULT", "fields": fields, "values": values}


def _apply_size_rules(signals: dict, policy: dict, tier: str, reasons: list) -> tuple[str, bool]:
    heavy_fields = [field for field, threshold in (
        ("stories", policy["heavy_stories"]), ("existing_files", policy["heavy_existing"]),
        ("max_references", policy["heavy_references"]))
        if signals[field] is not None and signals[field] >= threshold]
    if heavy_fields:
        reasons.append(_reason("HEAVY-SIZE", heavy_fields, signals))
        tier = "heavy"
    split_fields = []
    if signals["stories"] is not None and signals["stories"] > policy["split_stories"]:
        split_fields.append("stories")
    files = _total_files(signals)
    if files is not None and files > policy["split_files"]:
        split_fields.extend(["existing_files", "created_files"])
    if signals["interfaces"] is not None and signals["interfaces"] > policy["split_interfaces"]:
        split_fields.append("interfaces")
    if split_fields:
        reasons.append(_reason("SPLIT-SIZE", split_fields, signals))
        tier = "heavy"
    return tier, bool(split_fields)


def _apply_risk_rules(signals: dict, tier: str, reasons: list) -> str:
    heavy = [f"risks.{field}" for field in HEAVY_RISKS if signals["risks"][field] == "yes"]
    standard = [f"risks.{field}" for field in STANDARD_RISKS if signals["risks"][field] == "yes"]
    if standard:
        tier = max_tier(tier, "standard")
        reasons.append(_reason("STANDARD-FLOOR", standard, signals))
    if heavy:
        tier = "heavy"
        reasons.append(_reason("HEAVY-RISK", heavy, signals))
    return tier


def _reason(rule_id: str, fields: list[str], signals: dict) -> dict:
    return {"rule_id": rule_id, "fields": fields,
            "values": [_field_value(signals, field) for field in fields]}


def _field_value(signals: dict, field: str) -> object:
    if field.startswith("risks."):
        return signals["risks"][field.removeprefix("risks.")]
    return signals[field]


def _total_files(signals: dict) -> int | None:
    if signals["existing_files"] is None or signals["created_files"] is None:
        return None
    return signals["existing_files"] + signals["created_files"]
